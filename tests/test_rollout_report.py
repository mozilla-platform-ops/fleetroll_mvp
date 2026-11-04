"""Rollout reporting across master, multiple overrides, and uncertain evidence."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import shlex
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from fleetroll import rollout_report as report
from fleetroll.cli import cli
from fleetroll.db import insert_host_observation

from tests.tmux_helpers import TmuxSession, skip_no_tmux

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
    clients.queue.taskQueueCounts.return_value = {"pendingTasks": 12}
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


def test_live_queue_counts_separate_pools_and_preserve_report_on_failure(env, capsys):
    seed(env, "master")
    seed(env, "second")
    seed(env, "isolated", tc_worker_identity=identity("isolated", pool="isolated-pool"))
    env.clients.queue.taskQueueCounts.side_effect = [
        RuntimeError("queue unavailable"),
        {"pendingTasks": 1234},
    ]
    assert invoke(env, "--role", ROLE) == 0
    result = read(capsys)
    counts = result["queue_counts"]
    assert len(counts["pools"]) == 2
    assert env.clients.queue.taskQueueCounts.call_count == 2
    assert counts["pools"][0]["pending_tasks"] is None
    assert counts["pools"][1]["pending_tasks"] == 1234
    report.print_report(result, color=False)
    output = capsys.readouterr().out
    assert "releng-hardware/talos-pool: 1,234 pending" in output
    assert "releng-hardware/isolated-pool: unavailable" in output


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
    report.print_report(result, color=False)
    output = capsys.readouterr().out
    assert "⚠ WARNING: 1 host(s): latest host observation failed" in output
    assert "may be a reboot" in output
    assert "Worker jobs were not queried" in " ".join(output.split())
    assert "✕ ERROR: host: latest host observation failed" not in output


def test_old_failed_observation_remains_an_error(env, capsys):
    seed(env, "host", stamp=UNTIL - dt.timedelta(minutes=10))
    insert_host_observation(
        env.conn,
        {
            "host": "host",
            "ts": (UNTIL - dt.timedelta(minutes=6)).isoformat(),
            "action": "host.audit",
            "ok": False,
            "observed": {},
        },
    )
    env.conn.commit()
    assert invoke(env, "--role", ROLE) == 1
    result = read(capsys)
    report.print_report(result, color=False)
    assert "✕ ERROR: 1 host(s): latest host observation failed" in capsys.readouterr().out


def test_issues_group_hosts_and_verbose_retains_details(capsys):
    names = [f"worker-{n:03d}.example.com" for n in (1, 2, 3, 5)]
    result = {
        "hosts": [
            {"host": name, "error": "latest host observation failed", "observation_age_s": 10}
            for name in names
        ],
        "warnings": [],
        "errors": [f"{name}: latest host observation failed" for name in names],
    }

    def style(value, **kwargs):
        return value

    report.print_issues(result, style=style)
    output = capsys.readouterr().out
    assert "4 host(s): latest host observation failed" in output
    assert "worker-[001-003, 005].example.com" in output
    assert "Worker jobs were not queried" in output
    report.print_issues(result, style=style, verbose=True)
    output = capsys.readouterr().out
    for name in names:
        assert name in output


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


@pytest.mark.parametrize("selection", ["talos-pool", "releng-hardware/talos-pool"])
def test_pool_selector_forms_select_same_hosts(env, capsys, selection):
    seed(env, "host")
    assert invoke(env, "--worker-type", selection) == 0
    assert [host["host"] for host in read(capsys)["hosts"]] == ["host"]


def test_fleetroll_rollout_report_delegates_and_preserves_exit_code(monkeypatch):
    seen = []
    monkeypatch.setattr(report, "main", lambda args, **kw: seen.append(args) or 1)
    result = CliRunner().invoke(cli, ["rollout-report", "--role", ROLE, "--min-finished", "10"])
    assert result.exit_code == 1
    assert seen == [["--role", ROLE, "--min-finished", "10"]]


def test_fleetroll_rollout_report_help():
    result = CliRunner().invoke(cli, ["rollout-report", "--help"], prog_name="fleetroll")
    assert result.exit_code == 0
    assert "fleetroll rollout-report" in result.output
    assert "--min-finished" in result.output


def test_fleetroll_rollout_report_runs_with_json(env):
    seed(env, "host")
    result = CliRunner().invoke(
        cli,
        [
            "rollout-report",
            "--role",
            ROLE,
            "--db",
            str(env.db),
            "--audit-log",
            str(env.audit),
            "--since",
            SINCE.isoformat(),
            "--until",
            UNTIL.isoformat(),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert [host["host"] for host in json.loads(result.output)["hosts"]] == ["host"]


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
        ["host", "--min-host-coverage", "nan"],
        ["host", "--min-host-coverage", "101"],
        ["host", "--min-host-coverage", "-1"],
        ["--worker-type", "/pool"],
        ["--worker-type", "a/b/c"],
        ["--worker-type", "other/pool", "--provisioner", "releng-hardware"],
    ],
)
def test_invalid_arguments_exit_two(args):
    with pytest.raises(SystemExit) as exc:
        report.parse_args(args)
    assert exc.value.code == 2


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
    assert "1 selected hosts" in text
    assert "Completed: 0" in text
    assert "Running: 0" in text
    assert "unknown configuration" in text
    assert "run 0:" not in text
    report.print_report(result, verbose=True)
    verbose = capsys.readouterr().out
    assert "run 0:" in verbose
    assert "Unknown configuration:" in verbose


def test_unverified_host_messages_grouped_by_reason(capsys):
    result = {
        "hosts": [
            {"host": "host-b", "configuration_reason": "waiting for Puppet"},
            {"host": "host-a", "configuration_reason": "waiting for Puppet"},
            {"host": "host-c", "configuration_reason": "unknown role"},
            {"host": "host-d", "configuration_reason": "query failed"},
        ],
        "errors": ["host-d: query failed"],
    }
    report.print_unverified_hosts(result, details=False)
    text = capsys.readouterr().out
    assert "2 host(s): waiting for Puppet" in text
    assert "1 host(s): unknown role" in text
    assert "host-a" not in text
    assert "query failed" not in text
    assert "--details or --verbose" in text
    report.print_unverified_hosts(result, details=True)
    text = capsys.readouterr().out
    assert "host-a" in text
    assert "host-b" in text
    assert "host-c" in text
    assert "host-d" not in text


def test_observed_outcomes_include_unattributed_failures(env, capsys):
    seed(env, "host")
    task(env, "host", state="completed", minute=45)
    task(env, "host", state="failed", minute=10)
    assert invoke(env, "host") == 0  # Unattributed outcomes do not change cohort exit status.
    result = read(capsys)
    report.print_report(result, color=False)
    text = capsys.readouterr().out
    assert "Configuration coverage: 1/2" in text
    assert "Full report window: 1 excluded (1 failed, 0 exceptions, 0 unknown outcomes)" in text
    assert "All observed in-window runs" not in text
    report.print_report(result, details=True, color=False)
    details = capsys.readouterr().out
    assert "All observed in-window runs: 2 (across configurations)" in details
    assert "Completed: 1  ·  Failed: 1  ·  Exceptions: 0" in details
    assert "Attributed runs: 1\n      Completed: 1  ·  Failed: 0" in details
    assert "1: unknown configuration or task spans a transition/observation gap" in details
    assert result["summary"]["cohorts"][0]["outcomes"]["failed"] == 0


def test_fleet_percentages_include_unapplied_override_removal(env, capsys):
    sha = override(env, "branch")
    seed(env, "override", sha=sha, git="test_branch")
    seed(env, "removed", puppet_override_sha_applied=sha)
    assert invoke(env, "--role", ROLE) == 0
    result = read(capsys)
    status = result["fleet_override_status"]
    assert status["matching_hosts"] == 2
    assert status["override_totals"]["present"] == {"count": 1, "percentage": 50.0}
    assert status["override_totals"]["absent"] == {"count": 1, "percentage": 50.0}
    assert "removed" in result["summary"]["unverified_hosts"]
    report.print_report(result, color=False)
    output = capsys.readouterr().out
    assert "Override files: 1 present · 1 absent · 0 unknown" in output
    assert "override presence does not prove Puppet applied it" in " ".join(output.split())


def test_explicit_hosts_fleet_percentage_does_not_include_other_hosts(env, capsys):
    seed(env, "selected")
    seed(env, "other", sha=override(env, "branch"), git="test_branch")
    assert invoke(env, "selected") == 0
    status = read(capsys)["fleet_override_status"]
    assert status["matching_hosts"] == 1
    assert status["override_totals"]["absent"]["percentage"] == 100.0


def test_fleet_percentage_marks_failed_refresh_and_later_write(env, capsys):
    seed(env, "host", stamp=UNTIL - dt.timedelta(minutes=10))
    failed = {
        "host": "host",
        "ts": (UNTIL - dt.timedelta(minutes=2)).isoformat(),
        "action": "host.audit",
        "ok": False,
        "observed": {},
    }
    insert_host_observation(env.conn, failed)
    env.conn.commit()
    env.audit.write_text(
        json.dumps(
            {
                "host": "host",
                "ts": (UNTIL - dt.timedelta(minutes=5)).isoformat(),
                "action": "host.unset_override",
                "ok": True,
            }
        )
        + "\n"
    )
    assert invoke(env, "--role", ROLE) == 1
    status = read(capsys)["fleet_override_status"]
    assert status["newer_failed_gathers"] == ["host"]
    assert status["unverified_writes"] == ["host"]


def test_verbose_option_does_not_change_json(env, capsys):
    seed(env, "host")
    task(env, "host", minute=10)
    invoke(env, "host", "-v")
    assert read(capsys)["hosts"][0]["jobs"]


def test_report_uses_color_only_when_enabled_and_keeps_verdict_readable(env, capsys, monkeypatch):
    seed(env, "host")
    task(env, "host", minute=10)
    env.clients.queue.taskQueueCounts.return_value = {"pendingTasks": 0}
    invoke(env, "host")
    result = read(capsys)
    report.print_report(result, color=True)
    colored = capsys.readouterr().out
    assert "\x1b[" in colored
    assert "Comparison unavailable" in colored
    queue_line = next(line for line in colored.splitlines() if "talos-pool: 0 pending" in line)
    assert "\x1b[33m" in queue_line
    assert "⚠" in queue_line
    assert "no new work queued" in queue_line
    assert colored.index("CURRENT QUEUE") < colored.index("Comparison unavailable")
    assert colored.index("CURRENT QUEUE") < colored.index("COHORT HEALTH")
    monkeypatch.setenv("NO_COLOR", "1")
    report.print_report(result)
    plain = capsys.readouterr().out
    assert "\x1b[" not in plain
    assert "Comparison unavailable" in plain
    assert "1 excluded" in plain
    assert "⚠ releng-hardware/talos-pool: 0 pending — no new work queued" in plain


def test_low_coverage_override_is_a_prominent_warning_with_comparable_sample(env, capsys):
    seed(env, "master")
    seed(env, "override", sha=override(env, "branch"), stamp=SINCE + dt.timedelta(minutes=60))
    for _ in range(15):
        task(env, "master", minute=80)
    task(env, "override", minute=100)
    task(env, "override", minute=70, state="failed")
    for _ in range(2):
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
    comparison = result["comparisons"][0]
    assert comparison["status"] == "comparable", comparison["reasons"]
    assert comparison["observed_result"] == "no regression observed in verified tasks"
    assert comparison["sample_status"] == "sufficient"
    assert comparison["attribution"]["status"] == "limited"
    assert comparison["reasons"] == []
    assert "override: configuration attribution coverage 25.0%" in "\n".join(comparison["warnings"])
    assert len(comparison["unattributed_failures"]) == 1
    assert comparison["unattributed_failures"][0]["outcome"] == "failed"
    report.print_report(result, color=True, details=True)
    output = capsys.readouterr().out
    assessment = output.split("COHORT HEALTH")[1].split("EVIDENCE LIMITS")[0]
    assert "Same observed failure and exception rates" in assessment
    assert "1 unattributed failure inside the comparison window" in assessment
    assert "override 25.0%" in assessment
    assert "\x1b[33m" in assessment
    assert "https://firefox-ci-tc.services.mozilla.com/tasks/" in output


@pytest.mark.parametrize(
    ("master_state", "override_state", "expected"),
    [
        ("completed", "completed", "no regression observed in verified tasks"),
        ("failed", "failed", "no regression observed in verified tasks"),
        ("failed", "completed", "no regression observed in verified tasks"),
        ("completed", "failed", "higher override failure rate observed"),
        ("completed", "exception", "higher override exception rate observed"),
        ("exception", "completed", "no regression observed in verified tasks"),
        ("failed", "exception", "higher override exception rate observed"),
    ],
)
def test_observed_comparison_result_uses_verified_rates(
    env, capsys, master_state, override_state, expected
):
    seed(env, "master")
    seed(env, "override", sha=override(env, "branch"))
    task(env, "master", minute=80, state=master_state)
    task(env, "override", minute=80, state=override_state)
    assert invoke(env, "--role", ROLE, min_finished=1) == int(
        master_state != "completed" or override_state != "completed"
    )
    result = read(capsys)
    comparison = result["comparisons"][0]
    assert comparison["observed_result"] == expected
    assert comparison["attribution"]["status"] == "meets threshold"
    assert comparison["sample_status"] == "sufficient"
    health_by_kind = {
        cohort["configuration"]["kind"]: cohort["health"]["status"]
        for cohort in result["summary"]["cohorts"]
    }
    expected_health = {
        "completed": "healthy in verified sample",
        "failed": "failures observed",
        "exception": "exceptions observed",
    }
    assert health_by_kind == {
        "master": expected_health[master_state],
        "override": expected_health[override_state],
    }
    report.print_report(result, color=False)
    text = capsys.readouterr().out
    if master_state == "failed" and override_state == "completed":
        assert "Master failure rate higher in verified tasks" in text
        assert "override - master: -100.00 pp" in text
    elif master_state == "completed" and override_state == "failed":
        assert "Override failure rate higher in verified tasks" in text
        assert "override - master: +100.00 pp" in text
    elif master_state == "exception":
        assert "Master exception rate higher in verified tasks" in text
    elif override_state == "exception":
        assert "Override exception rate higher in verified tasks" in text
    else:
        assert "Same observed failure and exception rates" in text


@pytest.mark.parametrize("kind", ["master", "override"])
def test_cohort_health_does_not_require_a_control_or_direction(env, capsys, kind):
    seed(env, "solo", sha=override(env, "branch") if kind == "override" else None)
    for _ in range(20):
        task(env, "solo", minute=80)
    assert invoke(env, "--role", ROLE) == 0
    result = read(capsys)
    cohort = result["summary"]["cohorts"][0]
    assert cohort["health"]["status"] == "healthy in verified sample"
    assert cohort["health"]["minimum_finished"] == 20
    assert result["comparisons"][0]["observed_result"] == "comparison unavailable"
    report.print_report(result, color=False)
    assessment = capsys.readouterr().out.split("COHORT HEALTH")[1].split("EVIDENCE LIMITS")[0]
    label = "master" if kind == "master" else "branch"
    assert label in assessment
    assert "Healthy in verified sample" in assessment
    assert assessment.index("Healthy in verified sample") < assessment.index(
        "Comparison unavailable"
    )


@pytest.mark.parametrize(
    ("states", "minimum", "expected_status"),
    [
        ([], 20, "insufficient sample"),
        (["completed"] * 19, 20, "insufficient sample"),
        (["completed"] * 20, 20, "healthy in verified sample"),
        (["completed", "unknown"], 1, "unknown outcomes"),
        (["failed"], 20, "failures observed"),
        (["exception"], 20, "exceptions observed"),
        (["failed", "exception"], 20, "failures and exceptions observed"),
    ],
)
def test_cohort_health_uses_absolute_outcomes_and_sample_floor(
    env, capsys, states, minimum, expected_status
):
    seed(env, "host")
    for state in states:
        task(env, "host", minute=80, state=state)
    assert invoke(env, "host", min_finished=minimum) == int(
        any(state in {"failed", "exception", "unknown"} for state in states)
    )
    result = read(capsys)
    health = result["summary"]["cohorts"][0]["health"]
    assert health["status"] == expected_status
    assert health["basis"] == "verified tasks in report window"
    report.print_report(result, color=False)
    assert expected_status.capitalize() in capsys.readouterr().out


def test_small_samples_do_not_emit_positive_observed_result(env, capsys):
    seed(env, "master")
    seed(env, "override", sha=override(env, "branch"))
    for host in ("master", "override"):
        task(env, host, minute=80)
    assert invoke(env, "--role", ROLE) == 0
    comparison = read(capsys)["comparisons"][0]
    assert comparison["sample_status"] == "insufficient"
    assert comparison["observed_result"] == "comparison unavailable"
    assert comparison["attribution"]["status"] == "meets threshold"


def test_unattributed_failure_outside_shared_window_is_reported_separately(env, capsys):
    seed(env, "master")
    seed(env, "override", sha=override(env, "branch"))
    for host in ("master", "override"):
        task(env, host, minute=80)
    task(env, "override", minute=10, state="failed")
    assert invoke(env, "--role", ROLE, min_finished=1) == 0
    result = read(capsys)
    comparison = result["comparisons"][0]
    assert comparison["unattributed_failures"] == []
    assert comparison["observed_result"] == "no regression observed in verified tasks"
    assert result["summary"]["unattributed_outcomes"]["outcomes"]["failed"] == 1
    assert result["summary"]["evidence"]["attribution"]["status"] == "limited"
    assert result["summary"]["evidence"]["status"] == "sample available"
    report.print_report(result, color=False)
    output = capsys.readouterr().out
    assert "Full report window: 1 excluded (1 failed" in output
    assert "unattributed failure inside the comparison window" not in output


@pytest.mark.parametrize("missing_kind", ["unavailable", "unverified"])
def test_partial_host_evidence_warns_without_vetoing_valid_comparison(env, capsys, missing_kind):
    sha = override(env, "branch")
    for number in range(8):
        host = f"healthy-{number}"
        seed(env, host, sha=sha if number % 2 else None)
        task(env, host, minute=80)
    if missing_kind == "unavailable":
        for number in range(2):
            host = f"unavailable-{number}"
            seed(env, host, stamp=UNTIL - dt.timedelta(minutes=10))
            insert_host_observation(
                env.conn,
                {"host": host, "ts": UNTIL.isoformat(), "ok": False, "observed": {}},
            )
        env.conn.commit()
    else:
        seed(env, "unverified", puppet_success=False)
        task(env, "unverified", minute=80)
    assert invoke(env, "--role", ROLE, min_finished=1) == int(missing_kind == "unavailable")
    result = read(capsys)
    evidence = result["summary"]["evidence"]
    assert evidence["status"] == "sample available"
    assert evidence["reasons"] == []
    assert evidence["warnings"]
    comparison = result["comparisons"][0]
    assert comparison["status"] == "comparable"
    assert comparison["reasons"] == []
    assert comparison["warnings"]
    report.print_report(result, color=False)
    output = capsys.readouterr().out
    assert "current host configuration(s) unverified" in output
    if missing_kind == "unavailable":
        assert "Hosts queried: 8/10; 2 hosts not queried" in output


def test_host_query_threshold_still_blocks_severely_incomplete_population(env, capsys):
    sha = override(env, "branch")
    seed(env, "master")
    seed(env, "override", sha=sha)
    for host in ("master", "override"):
        task(env, host, minute=80)
    seed(env, "offline", stamp=SINCE)
    assert invoke(env, "--role", ROLE, min_finished=1) == 1
    result = read(capsys)
    assert result["summary"]["evidence"]["status"] == "insufficient evidence"
    assert result["comparisons"][0]["status"] == "insufficient evidence"
    assert "host query coverage 66.7% below required 80.0%" in result["comparisons"][0]["reasons"]
    assert invoke(env, "--role", ROLE, min_finished=1, min_host_coverage=60) == 1
    result = read(capsys)
    assert result["comparisons"][0]["status"] == "comparable"
    assert result["summary"]["evidence"]["status"] == "sample available"


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


@pytest.mark.parametrize("explicit", [False, True])
def test_role_history_scope_preserves_unknown_runs_and_explicit_host_history(env, capsys, explicit):
    old_role = "gecko_t_linux_talos"
    seed(
        env,
        "migrated",
        stamp=SINCE + dt.timedelta(hours=1),
        role=old_role,
        puppet_role=old_role,
    )
    seed(
        env,
        "migrated",
        early=False,
        puppet_state_ts=(SINCE + dt.timedelta(minutes=75)).isoformat(),
    )
    old_task = task(env, "migrated", minute=45, state="failed")
    gap_task = task(env, "migrated", minute=65, state="exception")
    task(env, "migrated", minute=90)
    selection = ["migrated"] if explicit else ["--role", ROLE]
    assert invoke(env, *selection, min_finished=1) == int(explicit)
    result = read(capsys)
    summary = result["summary"]
    assert summary["all_observed_outcomes"]["jobs"] == 3
    assert summary["unattributed_outcomes"]["outcomes"]["exception"] == 1
    assert summary["unattributed_outcomes"]["outcomes"]["failed"] == 0
    assert summary["coverage"]["fetched"] == 3
    assert summary["coverage"]["fraction"] == pytest.approx(2 / 3 if explicit else 1 / 2)
    assert summary["coverage"]["out_of_scope"] == (0 if explicit else 1)
    assert summary["coverage"]["excluded"] == 1
    assert {group["configuration"]["role"] for group in summary["cohorts"]} == (
        {ROLE, old_role} if explicit else {ROLE}
    )
    if explicit:
        assert "out_of_scope_history" not in result["hosts"][0]
        return
    host = result["hosts"][0]
    assert [job["task_id"] for job in host["out_of_scope_history"]["jobs"]] == [old_task]
    assert next(job for job in host["jobs"] if job["task_id"] == gap_task)["configuration"] is None
    assert summary["out_of_scope_outcomes"]["outcomes"]["failed"] == 1
    assert summary["evidence"]["sample_status"] == "sufficient"
    assert all(pair["scope"][0] == ROLE for pair in result["comparisons"])
    report.print_report(result, color=False)
    output = capsys.readouterr().out
    assert old_role not in output
    assert "1 in-window runs" in output
    assert "Known out-of-scope runs: 1 (1 failed" in output
    report.print_report(result, details=True, color=False)
    output = capsys.readouterr().out
    assert f"omitted historical population: {old_role}" in output
    assert f"Outside selection: failed: {old_task}" in output


@pytest.mark.parametrize("selection", ["role", "pool", "provisioner"])
def test_empty_old_populations_do_not_limit_selected_report(env, capsys, selection):
    old_identity = identity("migrated")
    old_role = ROLE
    if selection == "role":
        old_role = "gecko_t_linux_talos"
        old_identity["workerType"] = "gecko-t-linux-talos-1804"
    elif selection == "pool":
        old_identity["workerType"] = "old-pool"
    else:
        old_identity["provisionerId"] = "old-provisioner"
    seed(
        env,
        "migrated",
        stamp=SINCE + dt.timedelta(hours=1),
        role=old_role,
        puppet_role=old_role,
        tc_worker_identity=old_identity,
    )
    seed(
        env,
        "migrated",
        early=False,
        puppet_state_ts=(SINCE + dt.timedelta(minutes=75)).isoformat(),
    )
    task(env, "migrated", minute=90)
    args = ["--role", ROLE] if selection == "role" else ["--worker-type", "talos-pool"]
    assert invoke(env, *args, min_finished=1) == 0
    result = read(capsys)
    assert len(result["summary"]["cohorts"]) == 1
    assert result["summary"]["coverage"]["fraction"] == 1
    assert result["summary"]["evidence"]["sample_status"] == "sufficient"
    assert len(result["comparisons"]) == 1
    omitted = result["hosts"][0]["out_of_scope_history"]
    assert len(omitted["intervals"]) == 1
    assert omitted["jobs"] == []
    assert omitted["intervals"][0]["configuration"]["scope"][1:3] == [
        old_identity["provisionerId"],
        old_identity["workerType"],
    ]


def test_role_scoping_applies_to_shared_window_evidence_and_failure_patterns(env, capsys):
    sha = override(env, "branch")
    seed(env, "master")
    seed(env, "stable", sha=sha, git="test_override_revision")
    seed(
        env,
        "migrated",
        stamp=SINCE + dt.timedelta(hours=1),
        role="old_role",
        puppet_role="old_role",
    )
    seed(
        env,
        "migrated",
        early=False,
        sha=sha,
        git="test_override_revision",
        puppet_state_ts=(SINCE + dt.timedelta(minutes=75)).isoformat(),
    )
    task(env, "master")
    task(env, "stable")
    task(env, "migrated", state="failed")
    unknown = task(env, "migrated", minute=65, state="exception")
    task(env, "migrated", minute=90)
    assert invoke(env, "--role", ROLE, min_finished=1) == 0
    result = read(capsys)
    assert result["summary"]["failure_patterns"] == []
    assert result["summary"]["evidence"]["sample_status"] == "sufficient"
    assert len(result["comparisons"]) == 1
    pair = result["comparisons"][0]
    assert pair["status"] == "comparable"
    assert pair["coverage"]["fraction"] == pytest.approx(3 / 4)
    assert pair["master_coverage"]["fraction"] == 1
    assert pair["override_coverage"]["fraction"] == pytest.approx(2 / 3)
    assert pair["master_results"]["finished"] == 1
    assert pair["override_results"]["finished"] == 2
    assert [job["task_id"] for job in pair["unattributed_failures"]] == [unknown]


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


def test_default_report_has_one_assessment_and_preserves_detail_access(env, capsys):
    seed(env, "master")
    sha = override(env, "branch")
    seed(env, "override", sha=sha)
    task(env, "master")
    task(env, "override")
    task(env, "override", minute=10, state="failed")
    invoke(env, "--role", ROLE)
    result = read(capsys)
    result["warnings"].append("Compact configuration history: 100 spans; retention cutoff test.")
    before = json.dumps(result, sort_keys=True)
    report.print_report(result, color=False, width=100)
    text = capsys.readouterr().out
    assert len(text.splitlines()) < 40
    assert text.count("COHORT HEALTH") == 1
    assert text.count("COMPARISON") == 1
    assert text.count("Comparison unavailable") == 1
    assert text.count(f"{ROLE}: releng-hardware/talos-pool") == 1
    assert "mdc1" not in text
    assert "No failures in verified tasks" in text
    assert "Full report window: 1 excluded (1 failed" in text
    assert "Applied revisions" not in text
    assert "All observed in-window runs" not in text
    assert "Compact configuration history" not in text
    assert sha[:12] not in text
    assert "█" not in text
    report.print_report(result, details=True, color=False, width=100)
    details = capsys.readouterr().out
    assert "Applied revisions" in details
    assert "All observed in-window runs" in details
    assert "Compact configuration history" in details
    assert "Checked:" in details
    assert sha[:12] in details
    assert "[mdc1]" in details
    assert json.dumps(result, sort_keys=True) == before


@pytest.mark.parametrize("width", [40, 60, 80, 120])
def test_report_wraps_long_names_without_losing_outcomes(env, capsys, width):
    branch = "long_branch_" * 12
    seed(env, "override", sha=override(env, branch))
    task(env, "override", state="failed")
    task(env, "override", state="unknown")
    invoke(env, "override")
    result = read(capsys)
    report.print_report(result, color=False, width=width)
    text = capsys.readouterr().out
    assert all(len(line) <= width for line in text.splitlines())
    assert branch in "".join(text.split())
    assert "Failed" in text
    assert "Unknown" in text
    assert "Failures observed" in " ".join(text.split())
    assert "No failures in verified tasks" not in text
    assert "\r" not in text
    assert "\x1b[" not in text


@pytest.mark.parametrize("other_group", ["mdc1", "mdc2"])
def test_population_labels_show_groups_only_for_multiple_regions(env, capsys, other_group):
    seed(env, "one")
    seed(env, "two", tc_worker_identity=identity("two", group=other_group))
    invoke(env, "--role", ROLE)
    result = read(capsys)
    report.print_report(result, color=False, width=120)
    text = capsys.readouterr().out
    population = f"{ROLE}: releng-hardware/talos-pool"
    if other_group == "mdc1":
        assert text.count(population) == 1
        assert "mdc1" not in text
    else:
        assert population + " [mdc1]" in text
        assert population + " [mdc2]" in text
        assert text.count("COHORT HEALTH") == 2
        assert text.count("COMPARISON") == 2


def test_same_branch_different_overrides_are_disambiguated(env, capsys):
    first = override(env, "same-branch")
    content = (env.directory / first[:12]).read_text() + "# second version\n"
    second = hashlib.sha256(content.encode()).hexdigest()
    (env.directory / second[:12]).write_text(content)
    seed(env, "first", sha=first)
    seed(env, "second", sha=second)
    invoke(env, "--role", ROLE)
    result = read(capsys)
    report.print_report(result, color=False)
    text = capsys.readouterr().out
    assert "same-branch [1]" in text
    assert "same-branch [2]" in text
    assert first[:12] not in text
    assert second[:12] not in text
    report.print_report(result, details=True, color=False)
    details = capsys.readouterr().out
    assert first[:12] in details
    assert second[:12] in details


def test_tty_progress_finishes_with_carriage_return(env, capsys, monkeypatch):
    seed(env, "host")
    monkeypatch.setattr(report.sys.stderr, "isatty", lambda: True)
    invoke(env, "host")
    captured = capsys.readouterr()
    assert captured.err == "\rChecking workers 1/1\r\n"
    assert json.loads(captured.out)["summary"]["requested_hosts"] == 1


@pytest.mark.tui
@skip_no_tmux
@pytest.mark.parametrize(("width", "disabled_flag"), [(60, "ONLCR"), (120, "OPOST"), (100, None)])
def test_report_stays_aligned_with_disabled_tty_newline_translation(
    env, capsys, tmp_path, width, disabled_flag
):
    seed(env, "master")
    seed(env, "override", sha=override(env, "branch"))
    task(env, "master")
    task(env, "override")
    invoke(env, "--role", ROLE)
    result = read(capsys)
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(result))
    driver = tmp_path / "render.py"
    disable_newline = f"flags[1] &= ~termios.{disabled_flag}\n" if disabled_flag else ""
    driver.write_text(
        "import json, os, sys, termios\n"
        "from fleetroll.rollout_report import print_report\n"
        "fd = sys.stdout.fileno()\n"
        "flags = termios.tcgetattr(fd)\n"
        f"{disable_newline}"
        "termios.tcsetattr(fd, termios.TCSANOW, flags)\n"
        "before = termios.tcgetattr(fd)\n"
        # Demonstrate the drift caused by ordinary LF with translation disabled.
        "os.write(fd, b'BEFORE\\nNEXT\\n')\n"
        f"with open({str(report_path)!r}) as stream:\n"
        "    document = json.load(stream)\n"
        "sys.stderr.write('\\rChecking workers 2/2\\r\\n')\n"
        f"print_report(document, color=True, width={width})\n"
        "assert termios.tcgetattr(fd) == before\n"
        "os.write(fd, b'\\r\\nSAME_TERMIOS\\r\\n')\n"
        "input()\n"
    )
    with TmuxSession(
        cmd=f"uv run python {shlex.quote(str(driver))}", cols=width, rows=100
    ) as session:
        assert session.wait_for("SAME_TERMIOS", timeout=15), session.capture()
        screen = session.capture()
        if disabled_flag:
            assert "BEFORE\n      NEXT" in screen  # Reproduces the old LF-only drift.
        else:
            assert "BEFORE\nNEXT" in screen
        assert "\n◈ FLEETROLL / ROLLOUT REPORT\n" in screen
        assert "\n  EVIDENCE LIMITS\n" in screen
        assert "\n    COHORT HEALTH" in screen
        assert "\n    COMPARISON" in screen
        assert "\x1b[" in session.capture(with_escapes=True)


@pytest.mark.parametrize("details", [False, True])
def test_routine_evidence_limits_are_neutral_but_remain_visible(env, capsys, details):
    seed(env, "master")
    seed(env, "override", sha=override(env, "branch"))
    task(env, "master", minute=10)  # Unknown configuration, completed outcome.
    task(env, "override")
    invoke(env, "--role", ROLE)
    result = read(capsys)
    result["warnings"] = [
        "Compact configuration history: 50 spans; retention cutoff test.",
        "3 host(s) have a full 20-task window; earlier jobs may be omitted.",
    ]
    group = next(
        group
        for group in result["summary"]["cohorts"]
        if group["configuration"]["kind"] == "override"
    )
    group["applied_revisions"] = {"test_first_revision": 1, "test_second_revision": 1}
    report.print_report(result, color=True, details=details, width=120)
    colored = capsys.readouterr().out
    assert "\x1b[33m" not in colored
    assert "⚠" not in colored
    text = report.click.unstyle(colored)
    assert "Insufficient sample" in text
    assert "Comparison unavailable" in text
    assert "Attribution:" in text
    assert "below 80.0% warning threshold" in text
    assert "sample spans 2 applied revisions" in text
    assert "1 excluded (0 failed, 0 exceptions, 0 unknown outcomes)" in text
    assert "earlier jobs may be omitted" in text


@pytest.mark.parametrize("mode", ["default", "details", "verbose"])
def test_report_sections_and_diagnostics_have_consistent_indentation(env, capsys, mode):
    seed(env, "host", puppet_success=False)
    task(env, "host", minute=10, state="failed")
    invoke(env, "--role", ROLE)
    result = read(capsys)
    report.print_report(
        result, color=False, details=mode == "details", verbose=mode == "verbose", width=100
    )
    text = capsys.readouterr().out
    assert [line for line in text.splitlines() if line and not line.startswith(" ")] == [
        "◈ FLEETROLL / ROLLOUT REPORT"
    ]
    assert "\n  EVIDENCE LIMITS\n" in text
    assert "\n    COHORT HEALTH\n" in text
    assert "\n    Unverified host configurations:\n" in text
    if mode != "default":
        assert "\n  DETAILS\n" in text
        assert "\n    Host details\n" in text
    assert all(len(line) <= 100 for line in text.splitlines())


def test_actual_failures_and_empty_queue_keep_warning_color(env, capsys):
    seed(env, "host")
    task(env, "host", state="failed")
    task(env, "host", state="exception", minute=10)
    env.clients.queue.taskQueueCounts.return_value = {"pendingTasks": 0}
    invoke(env, "host")
    result = read(capsys)
    report.print_report(result, color=True)
    output = capsys.readouterr().out
    for phrase in ("Failures observed", "no new work queued", "Full report window"):
        assert "\x1b[33m" in next(line for line in output.splitlines() if phrase in line)
