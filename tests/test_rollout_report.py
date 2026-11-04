"""Rollout reporting across master, multiple overrides, and uncertain evidence."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fleetroll import rollout_report as report
from fleetroll.db import insert_host_observation

ROLE = "gecko_t_linux_2404_talos"
REPO = "mozilla-platform-ops/ronin_puppet"
MASTER = "test_master_revision"
SINCE = dt.datetime(2026, 11, 3, 10, tzinfo=dt.UTC)
UNTIL = SINCE + dt.timedelta(hours=2)


def identity(host, *, pool="talos-pool", group="mdc1"):
    return {
        "provisionerId": "releng-hardware",
        "workerType": pool,
        "workerGroup": group,
        "workerId": host,
    }


def observation(host, *, stamp=UNTIL, sha=None, git=MASTER, **changes):
    observed = {
        "role": ROLE,
        "puppet_role": ROLE,
        "override_present": bool(sha),
        "override_sha256": sha,
        "puppet_override_sha_applied": sha,
        "puppet_git_repo": f"https://github.com/{REPO}.git",
        "puppet_git_sha": git,
        "puppet_git_branch": "HEAD",
        "puppet_git_dirty": False,
        "puppet_success": True,
        "puppet_state_ts": (SINCE + dt.timedelta(minutes=30)).isoformat(),
        "vault_present": False,
        "tc_worker_identity_status": "ok",
        "tc_worker_identity": identity(host),
    }
    observed.update(changes)
    return {
        "host": host,
        "ts": stamp.isoformat(),
        "action": "host.audit",
        "ok": True,
        "observed": observed,
    }


@pytest.fixture
def env(tmp_path, temp_db, db_conn, monkeypatch):
    directory = tmp_path / "overrides"
    directory.mkdir()
    clients = SimpleNamespace(queue=Mock(), worker_manager=Mock())
    clients.worker_manager.getWorker.return_value = {"recentTasks": []}
    monkeypatch.setattr(report, "load_tc_credentials", Mock())
    monkeypatch.setattr(report, "create_tc_clients", Mock(return_value=clients))
    db_conn.execute(
        "INSERT INTO github_refs VALUES (?, ?, ?, ?, ?, ?)",
        (*REPO.split("/"), "master", SINCE.isoformat(), MASTER, "{}"),
    )
    db_conn.commit()
    return SimpleNamespace(
        directory=directory,
        audit=tmp_path / "audit.jsonl",
        db=temp_db,
        conn=db_conn,
        clients=clients,
        tasks={},
    )


def override(env, branch):
    content = f'PUPPET_REPO="https://github.com/{REPO}.git"\nPUPPET_BRANCH="{branch}"\n'
    sha = hashlib.sha256(content.encode()).hexdigest()
    (env.directory / sha[:12]).write_text(content)
    return sha


def seed(env, host, *, early=True, **kwargs):
    record = observation(host, **kwargs)
    if early and record["ts"] == UNTIL.isoformat():
        earlier = {**record, "ts": (UNTIL - dt.timedelta(hours=1)).isoformat()}
        insert_host_observation(env.conn, earlier, retention_limit=100)
    insert_host_observation(env.conn, record, retention_limit=100)
    env.conn.commit()
    return record


def task(env, host, *, name="test-benchmark", state="completed", minute=45, pool="talos-pool"):
    task_id = f"test_task_{len(env.tasks)}"
    status = {
        "status": {
            "provisionerId": "releng-hardware",
            "workerType": pool,
            "runs": [
                {
                    "runId": 0,
                    "workerId": host,
                    "workerGroup": "mdc1",
                    "state": state,
                    "started": (SINCE + dt.timedelta(minutes=minute)).isoformat(),
                    "resolved": (SINCE + dt.timedelta(minutes=minute + 1)).isoformat()
                    if state not in {"running", "pending"}
                    else None,
                }
            ],
        }
    }
    env.tasks[task_id] = (host, status, name)
    env.clients.worker_manager.getWorker.side_effect = lambda provisioner, pool, group, worker: {
        "recentTasks": [
            {"taskId": key, "runId": 0} for key, item in env.tasks.items() if item[0] == worker
        ]
    }
    env.clients.queue.status.side_effect = lambda key: env.tasks[key][1]
    env.clients.queue.task.side_effect = lambda key: {"metadata": {"name": env.tasks[key][2]}}
    return task_id


def invoke(env, *selection, **options):
    args = [
        *selection,
        "--db",
        str(env.db),
        "--audit-log",
        str(env.audit),
        "--since",
        SINCE.isoformat(),
        "--until",
        UNTIL.isoformat(),
    ]
    for key, value in options.items():
        args.extend(["--" + key.replace("_", "-"), str(value)])
    return report.main([*args, "--json"])


def read(capsys):
    return json.loads(capsys.readouterr().out)


def test_role_report_compares_each_override_with_master_and_separates_failures(env, capsys):
    seed(env, "master")
    seed(env, "override-a", sha=override(env, "branch-a"), git="test_branch_a")
    seed(env, "override-b", sha=override(env, "branch-b"), git="test_branch_b")
    task(env, "master", state="failed", name="shared-failure")
    task(env, "override-a", state="failed", name="shared-failure")
    task(env, "override-b", state="completed")
    assert invoke(env, "--role", ROLE, min_finished=1) == 1
    result = read(capsys)
    assert result["schema_version"] == 1
    assert len(result["summary"]["cohorts"]) == 3
    assert len(result["comparisons"]) == 2
    assert {pair["status"] for pair in result["comparisons"]} == {"comparable"}
    assert {pair["failure_rate_difference_pp"] for pair in result["comparisons"]} == {0, -100}
    assert result["summary"]["failure_patterns"][0]["seen_on_master"]
    assert result["summary"]["coverage"]["classified"] == 3


def test_post_removal_does_not_require_override_sha_and_excludes_preconvergence(env, capsys):
    seed(env, "master")
    env.audit.write_text(
        json.dumps(
            {
                "host": "master",
                "ts": (SINCE + dt.timedelta(minutes=5)).isoformat(),
                "action": "host.unset_override",
                "ok": True,
            }
        )
        + "\n"
    )
    task(env, "master", minute=10)
    task(env, "master", minute=40)
    assert invoke(env, "master") == 0
    result = read(capsys)
    assert result["summary"]["coverage"]["classified"] == 1
    assert result["summary"]["coverage"]["excluded"] == 1
    assert result["summary"]["cohorts"][0]["configuration"]["kind"] == "master"
    assert result["comparisons"][0]["status"] == "unavailable"


def test_unknown_jobs_are_excluded_not_passes_and_small_sample_is_qualified(env, capsys):
    seed(env, "master")
    seed(env, "override", sha=override(env, "branch"))
    task(env, "master", minute=5)
    task(env, "master", minute=45)
    task(env, "override", minute=45)
    assert invoke(env, "--role", ROLE) == 0
    result = read(capsys)
    assert result["summary"]["coverage"]["fraction"] == pytest.approx(2 / 3)
    assert result["summary"]["cohorts"][0]["rate_denominator"] == 1
    assert result["comparisons"][0]["status"] == "insufficient evidence"


def test_no_override_cohort_and_no_tasks_are_useful_not_errors(env, capsys):
    seed(env, "master")
    assert invoke(env, "--role", ROLE) == 0
    result = read(capsys)
    assert result["summary"]["empty_task_hosts"] == ["master"]
    assert result["summary"]["coverage"]["fraction"] is None
    assert result["summary"]["cohorts"][0]["success_rate"] is None
    assert result["errors"] == []


def test_override_only_with_no_master_is_useful(env, capsys):
    seed(env, "override", sha=override(env, "branch"))
    task(env, "override")
    assert invoke(env, "override") == 0
    result = read(capsys)
    assert result["summary"]["cohorts"][0]["jobs"] == 1
    assert result["comparisons"][0]["status"] == "unavailable"


def test_failed_latest_observation_is_selected_but_not_queried(env, capsys):
    seed(env, "host", stamp=UNTIL - dt.timedelta(minutes=10))
    bad = {
        "host": "host",
        "ts": UNTIL.isoformat(),
        "action": "host.audit",
        "ok": False,
        "observed": {},
    }
    insert_host_observation(env.conn, bad)
    env.conn.commit()
    assert invoke(env, "--role", ROLE) == 1
    result = read(capsys)
    assert result["summary"]["requested_hosts"] == 1
    assert result["summary"]["queried_hosts"] == 0
    env.clients.worker_manager.getWorker.assert_not_called()


def test_queue_failure_is_visible_and_reduces_coverage(env, capsys):
    seed(env, "host")
    task(env, "host")
    env.clients.queue.status.side_effect = RuntimeError("test queue unavailable")
    assert invoke(env, "host") == 1
    result = read(capsys)
    assert result["summary"]["coverage"]["unresolved"] == 1
    assert result["summary"]["coverage"]["fraction"] == 0
    assert "test queue unavailable" in result["errors"][0]


def test_full_recent_history_warns_even_when_only_one_task_is_in_window(env, capsys):
    seed(env, "host")
    for _ in range(19):
        task(env, "host", minute=-30)
    task(env, "host")
    assert invoke(env, "host") == 0
    result = read(capsys)
    assert result["summary"]["coverage"]["in_window"] == 1
    assert result["summary"]["truncated_hosts"] == ["host"]


def test_pool_selection_does_not_mix_roles_or_pools(env, capsys):
    seed(env, "master")
    seed(env, "other-role", role="other-role", puppet_role="other-role")
    seed(env, "other-pool", tc_worker_identity=identity("other-pool", pool="separate-pool"))
    assert invoke(env, "--worker-type", "talos-pool") == 0
    result = read(capsys)
    assert {host["host"] for host in result["hosts"]} == {"master", "other-role"}
    assert len(result["comparisons"]) == 2


def test_partial_query_failure_remains_in_report_and_invalidates_comparison(env, capsys):
    seed(env, "master")
    seed(env, "override", sha=override(env, "branch"))
    seed(env, "missing-worker")
    task(env, "master")
    task(env, "override")
    original = env.clients.worker_manager.getWorker.side_effect

    def fetch(provisioner, pool, group, worker):
        if worker == "missing-worker":
            raise RuntimeError("worker not found")
        return original(provisioner, pool, group, worker)

    env.clients.worker_manager.getWorker.side_effect = fetch
    assert invoke(env, "--role", ROLE, min_finished=1) == 1
    result = read(capsys)
    assert result["summary"]["queried_hosts"] == 2
    assert result["summary"]["requested_hosts"] == 3
    assert result["comparisons"][0]["status"] == "insufficient evidence"


def test_nonterminal_and_unknown_outcomes_never_improve_rates():
    result = report.outcome_summary([{"outcome": state} for state in report.OUTCOMES])
    assert result["finished"] == 3
    assert result["success_rate"] == pytest.approx(1 / 3)


def test_worker_identity_mismatch_is_not_attributed(env, capsys):
    seed(env, "host")
    key = task(env, "host")
    env.tasks[key][1]["status"]["runs"][0]["workerId"] = "other-worker"
    assert invoke(env, "host") == 1
    result = read(capsys)
    assert result["summary"]["coverage"]["classified"] == 0


def test_explicit_failed_identity_blocks_role_fallback(env, capsys):
    seed(env, "host", tc_worker_identity_status="unreadable")
    assert invoke(env, "host") == 1
    result = read(capsys)
    assert "identity collection failed" in result["errors"][0]
    env.clients.worker_manager.getWorker.assert_not_called()


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["host", "--role", ROLE],
        ["--role", ROLE, "--worker-type", "pool"],
        ["host", "--since", "bad"],
        ["host", "--workers", "0"],
        ["host", "--max-observation-gap", "nan"],
    ],
)
def test_invalid_arguments_exit_two(args):
    with pytest.raises(SystemExit) as exc:
        report.parse_args(args)
    assert exc.value.code == 2


def test_script_uses_report_implementation():
    root = Path(__file__).resolve().parents[1]
    assert runpy.run_path(str(root / "scripts/check_rollout_jobs.py"))["main"] is report.main


def test_human_summary_shows_totals_exclusions_and_details(env, capsys):
    seed(env, "host")
    task(env, "host", minute=10)
    invoke(env, "host")
    result = read(capsys)
    report.print_report(result, details=True)
    text = capsys.readouterr().out
    assert "0/1 in-window recent runs" in text
    assert "1 excluded" in text
    assert "master" in text
    assert "unknown configuration" in text
    assert "run 0:" not in text
    report.print_report(result, verbose=True)
    verbose = capsys.readouterr().out
    assert "run 0:" in verbose
    assert "Unknown configuration:" in verbose


def test_verbose_option_does_not_change_json(env, capsys):
    seed(env, "host")
    task(env, "host", minute=10)
    invoke(env, "host", "-v")
    assert read(capsys)["hosts"][0]["jobs"]


def test_one_low_coverage_override_cannot_hide_behind_large_master_sample(env, capsys):
    seed(env, "master")
    seed(env, "override", sha=override(env, "branch"))
    for _ in range(15):
        task(env, "master", minute=80)
    task(env, "override", minute=90)
    for _ in range(3):
        task(env, "override", minute=70)
    # Three tasks fall in an evidence gap, while the large master sample could
    # make a pooled coverage percentage look adequate.
    failed = {
        "host": "override",
        "action": "host.audit",
        "ts": (SINCE + dt.timedelta(minutes=65)).isoformat(),
        "ok": False,
    }
    env.audit.write_text(json.dumps(failed) + "\n")
    sha = next(path for path in env.directory.iterdir()).read_text()
    digest = hashlib.sha256(sha.encode()).hexdigest()
    seed(
        env,
        "override",
        early=False,
        stamp=SINCE + dt.timedelta(minutes=90),
        sha=digest,
        puppet_state_ts=(SINCE + dt.timedelta(minutes=80)).isoformat(),
    )
    seed(
        env,
        "override",
        early=False,
        sha=digest,
        puppet_state_ts=(SINCE + dt.timedelta(minutes=80)).isoformat(),
    )
    assert invoke(env, "--role", ROLE, min_finished=1) == 0
    result = read(capsys)
    assert result["comparisons"][0]["status"] == "insufficient evidence"


def test_comparison_without_intervals_in_requested_window_does_not_crash(env, capsys):
    seed(env, "master", stamp=UNTIL - dt.timedelta(minutes=15))
    seed(env, "override", stamp=UNTIL - dt.timedelta(minutes=15), sha=override(env, "branch"))
    assert invoke(env, "--role", ROLE, since=(UNTIL - dt.timedelta(minutes=10)).isoformat()) == 0
    result = read(capsys)
    assert result["comparisons"][0]["status"] == "unavailable"


def test_missing_old_master_reference_does_not_assume_no_override_is_master(env, capsys):
    seed(env, "host", git="test_revision_not_proven_master")
    task(env, "host")
    assert invoke(env, "host") == 0
    result = read(capsys)
    assert result["summary"]["coverage"]["classified"] == 0
    assert result["summary"]["unverified_hosts"] == ["host"]
    assert result["summary"]["evidence"]["status"] == "insufficient evidence"


def test_stale_observations_are_visible_and_not_queried(env, capsys):
    seed(env, "host", stamp=SINCE)
    assert invoke(env, "host") == 1
    result = read(capsys)
    assert "stale" in result["errors"][0]
    env.clients.worker_manager.getWorker.assert_not_called()


def test_unset_after_last_observation_invalidates_current_membership(env, capsys):
    seed(env, "host", stamp=UNTIL - dt.timedelta(minutes=10), sha=override(env, "branch"))
    event = {
        "host": "host",
        "ts": (UNTIL - dt.timedelta(minutes=5)).isoformat(),
        "action": "host.unset_override",
        "ok": True,
    }
    env.audit.write_text(json.dumps(event) + "\n")
    assert invoke(env, "host") == 0
    result = read(capsys)
    assert result["hosts"][0]["configuration"] is None
    assert "write after" in result["hosts"][0]["configuration_reason"]


def test_malformed_task_reference_is_retained_as_unresolved(env, capsys):
    seed(env, "host")
    env.clients.worker_manager.getWorker.return_value = {"recentTasks": [None]}
    assert invoke(env, "host") == 1
    result = read(capsys)
    assert result["summary"]["coverage"]["unresolved"] == 1


def test_role_selection_keeps_distinct_pools_separate(env, capsys):
    seed(env, "master")
    seed(
        env,
        "override",
        sha=override(env, "branch"),
        tc_worker_identity=identity("override", pool="isolated-pool"),
    )
    task(env, "master")
    task(env, "override", pool="isolated-pool")
    assert invoke(env, "--role", ROLE) == 0
    result = read(capsys)
    assert len(result["comparisons"]) == 2
    assert all(pair["status"] == "unavailable" for pair in result["comparisons"])


def test_changed_applied_revision_is_visible_and_qualifies_comparison(env, capsys):
    seed(env, "master")
    sha = override(env, "branch")
    seed(env, "override", sha=sha, git="test_old_revision", stamp=SINCE + dt.timedelta(hours=1))
    seed(
        env,
        "override",
        early=False,
        sha=sha,
        git="test_new_revision",
        puppet_state_ts=(SINCE + dt.timedelta(minutes=75)).isoformat(),
    )
    task(env, "master")
    task(env, "override", minute=45)
    task(env, "override", minute=90)
    assert invoke(env, "--role", ROLE, min_finished=1) == 0
    result = read(capsys)
    group = next(
        group
        for group in result["summary"]["cohorts"]
        if group["configuration"]["kind"] == "override"
    )
    assert set(group["applied_revisions"]) == {"test_old_revision", "test_new_revision"}
    assert (
        "override: multiple applied revisions in the comparison"
        in result["comparisons"][0]["reasons"]
    )


@pytest.mark.parametrize("shape", ["gather", "monitor", "single", "list"])
def test_saved_observations_restore_pruned_evidence_without_changing_db(env, capsys, shape):
    seed(env, "host", early=False, puppet_state_ts=(UNTIL - dt.timedelta(minutes=10)).isoformat())
    task(env, "host", minute=45)
    assert invoke(env, "host") == 0
    assert read(capsys)["summary"]["coverage"]["classified"] == 0
    earlier = observation("host", stamp=SINCE + dt.timedelta(hours=1))
    shapes = {
        "gather": {"results": [earlier]},
        "monitor": {"host": earlier},
        "single": earlier,
        "list": [earlier],
    }
    path = env.directory.parent / "historical-observations.json"
    path.write_text(json.dumps(shapes[shape]))
    before = env.conn.execute("SELECT count(*) FROM host_observations").fetchone()[0]
    assert invoke(env, "host", observations_file=path) == 0
    result = read(capsys)
    assert result["summary"]["coverage"]["classified"] == 1
    assert env.conn.execute("SELECT count(*) FROM host_observations").fetchone()[0] == before


def test_invalid_saved_observation_file_is_rejected(env, capsys):
    seed(env, "host")
    path = env.directory.parent / "not-observations.json"
    path.write_text('{"summary": {}}')
    assert invoke(env, "host", observations_file=path) == 2
    assert "invalid host observation" in capsys.readouterr().err
