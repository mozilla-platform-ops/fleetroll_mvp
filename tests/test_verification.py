"""Pure verification keeps configuration, source eligibility and TC activity distinct."""

import base64
import copy
import hashlib
import json
import os
import shlex
from datetime import UTC, datetime, timedelta

import pytest
from fleetroll.configuration_evidence import normalize_metadata
from fleetroll.verification import VerificationPolicy, evaluate_verification

from tests.tmux_helpers import _UV_PATH, TmuxSession, skip_no_tmux

NOW = datetime(2026, 11, 17, 2, tzinfo=UTC)
REPO = "mozilla-platform-ops/ronin_puppet"
IDENTITY = {
    "provisioner": "releng-hardware",
    "worker_type": "test-pool",
    "worker_group": "test-group",
    "worker_id": "worker001",
}
OVERRIDE_BYTES = f"PUPPET_REPO=https://github.com/{REPO}.git\nPUPPET_BRANCH=test-branch\n".encode()
OVERRIDE_SHA = hashlib.sha256(OVERRIDE_BYTES).hexdigest()
VAULT_SHA = hashlib.sha256(b"fake vault").hexdigest()


def stamp(*, seconds=300):
    return (NOW - timedelta(seconds=seconds)).isoformat()


def set_metadata(record, **updates):
    component = record["observed"]["configuration_evidence"]["components"]["puppet"]
    state = {**component.get("raw", {}), **updates}
    raw = json.dumps(state).encode()
    component.update(
        content_b64=base64.b64encode(raw).decode(), sha256=hashlib.sha256(raw).hexdigest()
    )
    component.update(normalize_metadata(component))


@pytest.fixture
def inputs():
    file_base = {
        "status": "present",
        "consistency": "stable",
        "observed_at": stamp(seconds=302),
        "confirmed_at": stamp(seconds=301),
        "attempt_id": "test-attempt",
        "reason": None,
        "file": {
            "device": 1,
            "inode": 1,
            "size": 1,
            "mtime_s": 1,
            "mtime_ns": 0,
            "ctime_s": 1,
            "ctime_ns": 0,
            "mode": 0o644,
            "uid": 0,
            "gid": 0,
        },
    }
    record = {
        "host": "worker.example.com",
        "ts": stamp(),
        "ok": True,
        "observed": {
            "os_type": "Linux",
            "tc_worker_identity_status": "ok",
            "tc_worker_identity": dict(
                zip(("provisionerId", "workerType", "workerGroup", "workerId"), IDENTITY.values())
            ),
            "configuration_evidence": {
                "schema_version": 1,
                "attempt": {
                    "id": "test-attempt",
                    "status": "completed",
                    "ssh_rc": 0,
                    "started_at": stamp(seconds=305),
                    "finished_at": stamp(),
                },
                "snapshot": {
                    "method": "double_read_v1",
                    "consistency": "stable",
                    "started_at": stamp(seconds=303),
                    "finished_at": stamp(seconds=301),
                },
                "components": {
                    "puppet": {**file_base, "path": "/etc/puppet/last_run_metadata.json"},
                    "override": {
                        **file_base,
                        "path": "/etc/puppet/ronin_settings",
                        "sha256": OVERRIDE_SHA,
                    },
                    "vault": {**file_base, "path": "/root/vault.yaml", "sha256": VAULT_SHA},
                },
                "retained_components": {},
            },
        },
    }
    set_metadata(
        record,
        schema_version=1,
        ts=stamp(seconds=86400),
        duration_s=10,
        success=True,
        exit_code=2,
        git_sha="a" * 40,
        git_repo=f"https://github.com/{REPO}.git",
        git_branch="HEAD",
        git_dirty=False,
        override_sha=OVERRIDE_SHA,
        vault_sha=VAULT_SHA,
    )
    expected = {
        "repository": REPO,
        "branch": "test-branch",
        "source": "override_artifact",
        "override_sha256": OVERRIDE_SHA,
        "ref_key": f"{REPO}:test-branch",
        "ts": stamp(),
        "sha": "a" * 40,
    }
    tc = {**IDENTITY, "ts": stamp(seconds=120), "last_date_active": stamp(seconds=180)}
    lookup = {
        "status": "success",
        "resolution_source": "host_configuration",
        "identity": dict(IDENTITY),
        "started_ts": stamp(seconds=130),
        "ts": stamp(seconds=120),
        "successful_observation_ts": tc["ts"],
    }
    return {
        "record": record,
        "expected_reference": expected,
        "tc_record": tc,
        "lookup_attempt": lookup,
    }


def evaluate(inputs, **options):
    return evaluate_verification(**inputs, now=NOW, **options)


def test_verified_is_configuration_only_and_evaluation_is_pure(inputs):
    original = copy.deepcopy(inputs)
    result = evaluate(inputs)
    assert result["configuration"]["state"] == "verified"
    assert result["components"]["activity"]["state"] == "recent"
    assert result["components"]["workload"]["state"] == "unknown"
    assert result["operation_authority"] == "not_granted"
    assert result["components"]["puppet"]["provenance"]["run"]["exclusive"] is False
    assert inputs == original
    inputs["tc_record"] = None
    inputs["lookup_attempt"] = None
    assert evaluate(inputs)["configuration"]["state"] == "verified"
    assert evaluate(inputs)["components"]["activity"]["state"] == "unknown"


def test_known_failure_remains_visible_with_unknown_requirement(inputs):
    set_metadata(inputs["record"], success=False, exit_code=4, vault_sha=None)
    result = evaluate(inputs)
    assert result["configuration"]["state"] == "not_verified"
    assert result["configuration"]["failing_requirements"] == ["puppet"]
    assert result["configuration"]["unknown_requirements"] == ["vault"]


@pytest.mark.parametrize("name", ["override", "vault"])
def test_full_hash_mismatch_not_display_prefix(inputs, name):
    component = inputs["record"]["observed"]["configuration_evidence"]["components"][name]
    component["sha256"] = component["sha256"][:12] + "f" * 52
    result = evaluate(inputs)
    assert result["components"][name]["state"] == "mismatch"
    assert result["configuration"]["state"] == "not_verified"


def test_case_insensitive_full_checksums(inputs):
    set_metadata(
        inputs["record"],
        override_sha=OVERRIDE_SHA.upper(),
        vault_sha=VAULT_SHA.upper(),
        git_sha="A" * 40,
    )
    assert evaluate(inputs)["configuration"]["state"] == "verified"


@pytest.mark.parametrize("name", ["override", "vault"])
def test_v1_null_and_absent_are_unknown_not_equal_or_inapplicable(inputs, name):
    envelope = inputs["record"]["observed"]["configuration_evidence"]
    envelope["components"][name].update(status="absent", sha256=None)
    set_metadata(inputs["record"], **{f"{name}_sha": None})
    result = evaluate(inputs)
    assert result["components"][name]["state"] == "unknown"
    assert "v1_null_is_ambiguous" in result["components"][name]["reasons"]
    assert result["configuration"]["state"] == "unknown"


@pytest.mark.parametrize("name", ["override", "vault"])
def test_absent_file_with_explicit_applied_hash_is_mismatch(inputs, name):
    inputs["record"]["observed"]["configuration_evidence"]["components"][name].update(
        status="absent", sha256=None
    )
    result = evaluate(inputs)
    assert result["components"][name]["state"] == "mismatch"
    assert result["configuration"]["state"] == "not_verified"


@pytest.mark.parametrize(("seconds", "state"), [(3599, "fresh"), (3600, "stale"), (-1, "unknown")])
def test_reference_freshness_boundaries_and_policy(inputs, seconds, state):
    inputs["expected_reference"]["ts"] = stamp(seconds=seconds)
    result = evaluate(inputs)
    assert result["freshness"]["reference"]["state"] == state
    assert result["components"]["revision"]["observed_state"] == "match"
    assert result["configuration"]["state"] == ("verified" if state == "fresh" else "unknown")
    if state == "stale":
        assert (
            evaluate(inputs, policy=VerificationPolicy(source_max_age_s=7200))["configuration"][
                "state"
            ]
            == "verified"
        )


@pytest.mark.parametrize("bad_time", [None, "invalid", "2026-11-17T01:00:00", stamp(seconds=-1)])
def test_invalid_host_timestamps_never_become_zero_age(inputs, bad_time):
    inputs["record"]["ts"] = bad_time
    result = evaluate(inputs)
    assert result["freshness"]["host"]["state"] == "unknown"
    assert result["freshness"]["host"]["age_seconds"] is None
    assert result["configuration"]["state"] == "unknown"


def test_stale_mismatch_is_retained_but_not_a_current_failure(inputs):
    inputs["expected_reference"].update(sha="b" * 40, ts=stamp(seconds=7200))
    result = evaluate(inputs)
    assert result["components"]["revision"]["observed_state"] == "mismatch"
    assert result["components"]["revision"]["state"] == "unknown"
    assert result["configuration"]["state"] == "unknown"


@pytest.mark.parametrize(
    "change",
    ["failed", "raced", "foreign_attempt", "unsupported", "contradiction", "dirty", "future_run"],
)
def test_unknown_prerequisites_never_produce_verification(inputs, change):
    record = inputs["record"]
    envelope = record["observed"]["configuration_evidence"]
    if change == "failed":
        record["ok"] = False
    elif change == "raced":
        envelope["snapshot"]["consistency"] = "changed"
    elif change == "foreign_attempt":
        envelope["components"]["vault"]["attempt_id"] = "earlier-attempt"
    elif change == "unsupported":
        set_metadata(record, schema_version=999)
    elif change == "contradiction":
        envelope["components"]["puppet"]["values"]["git_sha"] = "b" * 40
    elif change == "dirty":
        set_metadata(record, git_dirty=True)
    else:
        set_metadata(record, ts=stamp(seconds=-1))
    assert evaluate(inputs)["configuration"]["state"] == "unknown"


def test_retained_components_cannot_resurrect_current_success(inputs):
    envelope = inputs["record"]["observed"]["configuration_evidence"]
    envelope["retained_components"]["vault"] = envelope["components"]["vault"]
    envelope["components"]["vault"] = {"status": "unreadable", "reason": "read_failed"}
    envelope["attempt"]["status"] = "partial"
    envelope["snapshot"]["consistency"] = "unknown"
    result = evaluate(inputs)
    assert result["components"]["vault"]["observed_state"] == "match"
    assert result["components"]["vault"]["state"] == "unknown"
    assert result["configuration"]["state"] == "unknown"


def test_retained_components_from_different_attempts_are_not_combined(inputs):
    envelope = inputs["record"]["observed"]["configuration_evidence"]
    envelope["retained_components"]["vault"] = copy.deepcopy(envelope["components"]["vault"])
    envelope["retained_components"]["vault"]["attempt_id"] = "earlier-attempt"
    envelope["components"]["vault"] = {"status": "unreadable", "reason": "read_failed"}
    result = evaluate(inputs)
    assert result["components"]["vault"]["observed_state"] == "unknown"
    assert "file_run_attempt_mismatch" in result["components"]["vault"]["reasons"]


@pytest.mark.parametrize(
    "change", ["untyped_host", "untyped_transport", "completion_after_capture", "future_capture"]
)
def test_invalid_attempt_and_source_provenance_cannot_verify(inputs, change):
    envelope = inputs["record"]["observed"]["configuration_evidence"]
    if change == "untyped_host":
        inputs["record"]["ok"] = "yes"
    elif change == "untyped_transport":
        envelope["attempt"]["ssh_rc"] = False
    elif change == "completion_after_capture":
        set_metadata(inputs["record"], ts=stamp(seconds=10))
    else:
        envelope["snapshot"]["finished_at"] = stamp(seconds=-1)
    assert evaluate(inputs)["configuration"]["state"] == "unknown"


def test_unknown_requirement_does_not_hide_independent_fresh_mismatch(inputs):
    set_metadata(inputs["record"], vault_sha=None)
    inputs["expected_reference"]["sha"] = "b" * 40
    result = evaluate(inputs)
    assert result["configuration"]["state"] == "not_verified"
    assert result["configuration"]["failing_requirements"] == ["revision"]
    assert result["configuration"]["unknown_requirements"] == ["vault"]


@pytest.mark.parametrize(
    ("status", "state"),
    [
        ("pool_query_failed", "failed"),
        ("worker_missing", "missing_worker"),
        ("task_lookup_failed", "failed"),
    ],
)
def test_latest_failed_lookup_does_not_reuse_retained_tc_success(inputs, status, state):
    inputs["lookup_attempt"]["status"] = status
    result = evaluate(inputs)
    assert result["components"]["tc_lookup"]["state"] == state
    assert result["components"]["activity"]["state"] == "unknown"
    assert result["configuration"]["state"] == "verified"


@pytest.mark.parametrize(
    "change", ["unlinked", "identity", "historical", "stale", "future_activity", "naive_lookup"]
)
def test_tc_activity_needs_fresh_linked_exact_identity_evidence(inputs, change):
    if change == "unlinked":
        inputs["lookup_attempt"]["successful_observation_ts"] = stamp(seconds=119)
    elif change == "identity":
        inputs["tc_record"]["worker_group"] = "other-group"
    elif change == "historical":
        inputs["lookup_attempt"]["resolution_source"] = "historical_worker_identity"
    elif change == "stale":
        inputs["tc_record"]["ts"] = stamp(seconds=7200)
    elif change == "future_activity":
        inputs["tc_record"]["last_date_active"] = stamp(seconds=-1)
    else:
        inputs["lookup_attempt"]["ts"] = "2026-11-17T01:50:00"
    result = evaluate(inputs)
    assert result["components"]["activity"]["state"] == "unknown"
    assert result["configuration"]["state"] == "verified"


@pytest.mark.parametrize(("seconds", "state"), [(3599, "recent"), (3600, "not_recent")])
def test_activity_boundary_is_independent_of_configuration(inputs, seconds, state):
    inputs["tc_record"]["last_date_active"] = stamp(seconds=seconds)
    result = evaluate(inputs)
    assert result["components"]["activity"]["state"] == state
    assert result["configuration"]["state"] == "verified"


def test_windows_and_legacy_rows_do_not_gain_new_verification(inputs):
    inputs["record"]["observed"]["os_type"] = "Windows"
    assert evaluate(inputs)["configuration"]["state"] == "unknown"
    inputs["record"]["observed"].pop("configuration_evidence")
    assert evaluate(inputs)["configuration"]["state"] == "unknown"


def test_policy_and_evaluation_time_require_valid_windows(inputs):
    with pytest.raises(ValueError, match="positive"):
        VerificationPolicy(source_max_age_s=0)
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_verification(inputs["record"], now=NOW.replace(tzinfo=None))


def seed(inputs, *, tmp_path, monkeypatch):
    from fleetroll.commands.monitor.cache import ShaInfoCache
    from fleetroll.db import (
        get_connection,
        get_db_path,
        init_db,
        insert_github_ref,
        insert_host_observation,
        insert_tc_lookup_attempt,
        insert_tc_worker,
    )

    monkeypatch.setenv("HOME", str(tmp_path))
    path = get_db_path()
    init_db(path)
    cache = ShaInfoCache(path.parent / "overrides", path.parent / "vault_yamls")
    cache.overrides_dir.mkdir()
    (cache.overrides_dir / OVERRIDE_SHA[:16]).write_bytes(OVERRIDE_BYTES)
    cache.load_all()
    host = inputs["record"]["host"]
    owner, repo = REPO.split("/")
    with get_connection(path) as conn:
        insert_host_observation(conn, inputs["record"])
        insert_github_ref(
            conn,
            {
                "owner": owner,
                "repo": repo,
                "branch": "test-branch",
                "sha": "a" * 40,
                "ts": inputs["expected_reference"]["ts"],
            },
        )
        insert_tc_worker(conn, {**inputs["tc_record"], "host": host, "type": "worker"})
        insert_tc_lookup_attempt(conn, {**inputs["lookup_attempt"], "host": host})
        conn.commit()
    return path, cache


def freeze_evaluation(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr("fleetroll.commands.monitor.verification.datetime", Clock)
    monkeypatch.setattr("fleetroll.commands.monitor.json_output.datetime", Clock)


def test_cli_json_text_query_web_and_selection_share_the_evaluator(inputs, tmp_path, monkeypatch):
    from click.testing import CliRunner
    from fastapi.testclient import TestClient
    from fleetroll.cli import cli
    from fleetroll.commands.monitor.query import parse_query
    from fleetroll.commands.monitor.verification import evaluate_hosts
    from fleetroll.commands.web.app import create_app
    from fleetroll.selection import select_hosts

    seed(inputs, tmp_path=tmp_path, monkeypatch=monkeypatch)
    freeze_evaluation(monkeypatch)
    runner = CliRunner()
    host = inputs["record"]["host"]
    query = "config=verified activity=recent tc_lookup=ok sort:verify_age"
    result = runner.invoke(cli, ["host", "monitor", host, "--once", "--json", "--filter", query])
    assert result.exit_code == 0, result.output
    snapshot = json.loads(result.output)[host]
    assert snapshot["verification_schema_version"] == 1
    assert snapshot["worker_evidence_schema_version"] == 3
    assert snapshot["verification"] == evaluate(inputs)
    assert (
        snapshot["observed"]["configuration_evidence"]
        == inputs["record"]["observed"]["configuration_evidence"]
    )
    result = runner.invoke(cli, ["host", "monitor", host, "--once", "--filter", query])
    assert result.exit_code == 0, result.output
    assert "CONFIG" in result.output
    assert "verified" in result.output
    assert "recent" in result.output
    selection = select_hosts((host,), query=parse_query(query))
    assert selection.hosts == (host,)
    with TestClient(create_app()) as client:
        response = client.get("/api/hosts", params={"filter": query})
    assert response.status_code == 200, response.text
    row = response.json()["rows"][0]
    assert row["config"] == "verified"
    assert row["activity"] == "recent"
    assert row["verification"] == snapshot["verification"]
    batch = evaluate_hosts(
        [host, "other.example.com"],
        latest={host: inputs["record"]},
        tc_data={},
        refs={},
        sha_cache=None,
        now=NOW,
    )
    assert {value["evaluated_at"] for value in batch.values()} == {NOW.isoformat()}


def test_reference_resolution_requires_exact_artifact_bytes_and_branch_case(
    inputs, tmp_path, monkeypatch
):
    from fleetroll.commands.monitor.verification import expected_reference

    _, cache = seed(inputs, tmp_path=tmp_path, monkeypatch=monkeypatch)
    refs = {f"{REPO}:test-branch": {"sha": "a" * 40, "ts": stamp()}}
    assert expected_reference(inputs["record"], refs=refs, sha_cache=cache)["sha"] == "a" * 40
    assert (
        expected_reference(
            inputs["record"],
            refs={f"{REPO}:Test-branch": next(iter(refs.values()))},
            sha_cache=cache,
        )
        is None
    )
    (cache.overrides_dir / OVERRIDE_SHA[:16]).write_text("PUPPET_BRANCH=wrong-branch\n")
    assert expected_reference(inputs["record"], refs=refs, sha_cache=cache) is None


def test_follow_emits_reference_changes_but_not_ticking_age(inputs, tmp_path, monkeypatch):
    from fleetroll.commands.monitor.json_output import (
        build_monitor_json_snapshot,
        follow_monitor_json,
    )
    from fleetroll.commands.monitor.verification import comparison_record
    from fleetroll.data_provider import LocalProvider
    from fleetroll.db import get_connection, insert_github_ref

    path, cache = seed(inputs, tmp_path=tmp_path, monkeypatch=monkeypatch)
    freeze_evaluation(monkeypatch)
    host = inputs["record"]["host"]
    with get_connection(path) as conn:
        provider = LocalProvider(conn)
        latest, _ = provider.load_latest_records(hosts=[host])
        initial = build_monitor_json_snapshot(
            [host],
            latest=latest,
            tc_data=provider.load_tc_workers(hosts=[host]),
            sha_cache=cache,
            lookup_attempts=provider.load_tc_lookup_attempts(hosts=[host]),
            github_refs=provider.load_github_refs(),
            now=NOW,
        )
        before = initial[host]
        later = copy.deepcopy(before)
        later["verification"]["evaluated_at"] = (NOW + timedelta(seconds=1)).isoformat()
        later["verification"]["freshness"]["host"]["age_seconds"] += 1
        assert comparison_record(before) == comparison_record(later)
        owner, repo = REPO.split("/")
        insert_github_ref(
            conn,
            {
                "owner": owner,
                "repo": repo,
                "branch": "test-branch",
                "ts": stamp(seconds=299),
                "sha": "b" * 40,
            },
        )
        conn.commit()
        stream = follow_monitor_json([host], provider=provider, initial=initial, sha_cache=cache)
        update = next(stream)
        stream.close()
    assert update["verification"]["configuration"]["state"] == "not_verified"
    assert update["verification"]["components"]["revision"]["state"] == "mismatch"


@pytest.mark.parametrize(
    "change", ["version", "unknown_config", "lookup_failed", "identity_mismatch", "stale_reference"]
)
def test_example_consumer_refuses_unknown_prerequisites(inputs, change):
    def accepts_recent_verified_sample(payload):
        # A consumer display fixture, not permission to mutate or recover a host.
        verification = payload["verification"]
        return (
            payload["verification_schema_version"] == 1
            and verification["schema_version"] == 1
            and verification["configuration"]["state"] == "verified"
            and verification["components"]["activity"]["state"] == "recent"
            and verification["components"]["tc_lookup"]["state"] == "ok"
        )

    payload = {"verification_schema_version": 1, "verification": evaluate(inputs)}
    assert accepts_recent_verified_sample(payload)
    assert payload["verification"]["operation_authority"] == "not_granted"
    if change == "version":
        payload["verification_schema_version"] = 2
    else:
        if change == "unknown_config":
            set_metadata(inputs["record"], vault_sha=None)
        elif change == "lookup_failed":
            inputs["lookup_attempt"]["status"] = "pool_query_failed"
        elif change == "identity_mismatch":
            inputs["lookup_attempt"]["identity"]["worker_id"] = "other-worker"
        else:
            inputs["expected_reference"]["ts"] = stamp(seconds=3600)
        payload["verification"] = evaluate(inputs)
    assert not accepts_recent_verified_sample(payload)


@pytest.mark.tui
@skip_no_tmux
def test_tui_uses_verification_filter_and_shows_independent_activity(inputs, tmp_path, monkeypatch):
    # Bring collection/reference clocks to the real subprocess clock; the old
    # Puppet completion remains old and must not make current collection stale.
    delta = datetime.now(UTC) - NOW

    def shifted(value):
        return (datetime.fromisoformat(value) + delta).isoformat()

    record = inputs["record"]
    record["ts"] = shifted(record["ts"])
    envelope = record["observed"]["configuration_evidence"]
    for owner in (envelope["attempt"], envelope["snapshot"]):
        for key in ("started_at", "finished_at"):
            owner[key] = shifted(owner[key])
    for component in envelope["components"].values():
        for key in ("observed_at", "confirmed_at"):
            component[key] = shifted(component[key])
    for owner, keys in (
        (inputs["expected_reference"], ("ts",)),
        (inputs["tc_record"], ("ts", "last_date_active")),
        (inputs["lookup_attempt"], ("ts", "started_ts", "successful_observation_ts")),
    ):
        for key in keys:
            owner[key] = shifted(owner[key])
    seed(inputs, tmp_path=tmp_path, monkeypatch=monkeypatch)
    host = record["host"]
    cmd = f"{shlex.quote(_UV_PATH)} run fleetroll host monitor {host} --filter config=verified"
    with TmuxSession(
        cmd=cmd,
        cols=500,
        rows=40,
        env={"HOME": str(tmp_path), "PATH": os.environ["PATH"], "TERM": "xterm-256color"},
    ) as session:
        assert session.wait_for("hosts=1/1", timeout=15), session.capture()
        assert session.wait_for("CONFIG", timeout=5), session.capture()
        assert session.wait_for("verified", timeout=5), session.capture()
        assert session.wait_for("recent", timeout=5), session.capture()
        session.send_keys("/", "config=unknown", "Enter")
        assert session.wait_for("hosts=0/1", timeout=5), session.capture()
