"""Configuration history must not turn missing evidence into passing jobs."""

from __future__ import annotations

import datetime as dt
import hashlib

import pytest
from fleetroll.rollout_history import build_history, classify_run, verified_configuration

ROLE = "gecko_t_linux_2404_talos"
REPO = "mozilla-platform-ops/ronin_puppet"
MASTER = "test_master_revision"
BRANCH = "test_override_revision"


def time(hour, minute=0):
    return dt.datetime(2026, 11, 3, hour, minute, tzinfo=dt.UTC)


@pytest.fixture
def override(tmp_path):
    content = f'PUPPET_REPO="https://github.com/{REPO}.git"\nPUPPET_BRANCH="test-rollout"\n'
    sha = hashlib.sha256(content.encode()).hexdigest()
    (tmp_path / sha[:12]).write_text(content)
    return sha


def observation(hour, *, minute=0, applied=None, sha=None, git=None, **changes):
    observed = {
        "role": ROLE,
        "puppet_role": ROLE,
        "override_present": bool(sha),
        "override_sha256": sha,
        "puppet_override_sha_applied": sha,
        "puppet_success": True,
        "puppet_git_dirty": False,
        "puppet_git_sha": git or (BRANCH if sha else MASTER),
        "puppet_git_repo": f"ssh://git@ssh.github.com:443/{REPO}.git",
        "puppet_git_branch": "HEAD",
        "puppet_state_ts": (applied or time(hour, minute)).isoformat(),
        "vault_present": False,
    }
    observed.update(changes)
    return {
        "host": "host.example.com",
        "ts": time(hour, minute).isoformat(),
        "action": "host.audit",
        "ok": True,
        "observed": observed,
    }


def history(records, tmp_path):
    return build_history(
        records,
        overrides_dir=tmp_path,
        master_refs={(REPO, MASTER)},
        max_gap=dt.timedelta(hours=1),
    )


def classify(intervals, start, *, end=None):
    return classify_run(intervals, started=start, ended=end or start)


def test_removal_requires_convergence_and_excludes_transition_jobs(tmp_path, override):
    records = [
        observation(10, sha=override, applied=time(9, 50)),
        {
            "host": "host.example.com",
            "action": "host.unset_override",
            "ts": time(10, 5).isoformat(),
            "ok": True,
        },
        observation(10, minute=7, applied=time(9, 50), puppet_override_sha_applied=override),
        observation(10, minute=30, applied=time(10, 10)),
    ]
    intervals = history(records, tmp_path)
    assert classify(intervals, time(9, 55))[0].override_sha == override
    assert classify(intervals, time(10, 6))[0] is None
    assert classify(intervals, time(10, 15))[0].override_sha is None
    assert classify(intervals, time(9, 55), end=time(10, 15))[0] is None


def test_repeated_runs_preserve_activation_and_bound_future(tmp_path):
    intervals = history(
        [observation(10), observation(10, minute=30, applied=time(10, 25))], tmp_path
    )
    assert len(intervals) == 1
    assert intervals[0].start == time(10)
    assert intervals[0].end == time(10, 30)
    assert classify(intervals, time(10, 5))[0] is not None
    assert classify(intervals, time(10, 31))[0] is None


def test_manual_change_leaves_gap_and_splits_revisions(tmp_path, override):
    intervals = history(
        [
            observation(10, sha=override, applied=time(9, 50)),
            observation(10, minute=30, applied=time(10, 20)),
        ],
        tmp_path,
    )
    assert classify(intervals, time(10, 10))[0] is None
    assert classify(intervals, time(10, 25))[0].git_sha == MASTER


def test_long_observation_gap_is_not_bridged(tmp_path):
    intervals = history([observation(9), observation(12, applied=time(11, 50))], tmp_path)
    assert classify(intervals, time(10))[0] is None
    assert classify(intervals, time(11, 55))[0] is not None


def test_failed_observation_breaks_continuity(tmp_path):
    failed = {**observation(10, minute=5), "ok": False}
    intervals = history(
        [observation(10), failed, observation(10, minute=30, applied=time(10))], tmp_path
    )
    assert classify(intervals, time(10, 15))[0] is None
    assert classify(intervals, time(10, 30))[0] is not None


def test_write_attempt_invalidates_old_metadata_even_when_write_failed(tmp_path, override):
    write = {
        "action": "host.set_override",
        "ts": time(10, 5).isoformat(),
        "ok": False,
    }
    intervals = history(
        [
            observation(10, sha=override),
            write,
            observation(10, minute=10, sha=override, applied=time(10)),
        ],
        tmp_path,
    )
    assert classify(intervals, time(10, 8))[0] is None


def test_conflicting_same_timestamp_is_unknown(tmp_path, override):
    intervals = history([observation(10), observation(10, sha=override)], tmp_path)
    assert intervals == []


@pytest.mark.parametrize(
    "changes",
    [
        {"puppet_success": False},
        {"puppet_git_dirty": True},
        {"puppet_role": "other-role"},
        {"puppet_git_sha": "unrecognized_revision"},
        {"puppet_override_sha_applied": "unrecognized_override"},
        {"vault_present": True, "vault_sha256": "vault", "puppet_vault_sha_applied": "other"},
        {"tc_worker_identity_status": "missing"},
        {"puppet_state_ts": "2026-11-04T10:00:00Z"},
        {"override_present": None},
    ],
)
def test_unverified_configuration_is_not_master(tmp_path, changes):
    record = observation(10, **changes)
    config, reason = verified_configuration(
        record, overrides_dir=tmp_path, master_refs={(REPO, MASTER)}
    )
    assert config is None
    assert reason


def test_observed_worker_identity_is_preserved(tmp_path):
    identity = {
        "provisionerId": "another-provisioner",
        "workerType": "custom-pool",
        "workerGroup": "custom-group",
        "workerId": "different-worker",
    }
    config, _ = verified_configuration(
        observation(10, tc_worker_identity_status="ok", tc_worker_identity=identity),
        overrides_dir=tmp_path,
        master_refs={(REPO, MASTER)},
    )
    assert config.identity.worker_id == "different-worker"
    assert config.identity.worker_group == "custom-group"
    assert config.scope == (ROLE, "another-provisioner", "custom-pool", "custom-group")


def test_corrupt_override_content_is_not_trusted(tmp_path, override):
    (tmp_path / override[:12]).write_text('PUPPET_BRANCH="other"\n')
    assert history([observation(10, sha=override)], tmp_path) == []
