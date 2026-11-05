"""Explicit pool comparison preserves attribution, failures and workload differences."""

import json

import pytest
from click.testing import CliRunner
from fleetroll import rollout_compare as compare
from fleetroll.cli import cli

from tests.test_rollout_progress import Terminal
from tests.test_rollout_report import (
    SINCE,
    UNTIL,
    identity,
    make_env,
    override,
    seed,
    task,
)


@pytest.fixture
def env(tmp_path, temp_db, db_conn, monkeypatch):
    return make_env(tmp_path, temp_db=temp_db, db_conn=db_conn, monkeypatch=monkeypatch)


def pools(env):
    seed(env, "control")
    seed(
        env,
        "canary",
        sha=override(env, "upgrade"),
        tc_worker_identity=identity("canary", pool="candidate"),
    )


def arguments(env, *extra):
    return [
        "--candidate-pool",
        "candidate",
        "--baseline-pool",
        "talos-pool",
        "--db",
        str(env.db),
        "--audit-log",
        str(env.audit),
        "--since",
        SINCE.isoformat(),
        "--until",
        UNTIL.isoformat(),
        "--min-finished",
        "1",
        *extra,
    ]


def invoke(env, capsys, *extra):
    code = compare.main(arguments(env, "--json", *extra))
    return code, json.loads(capsys.readouterr().out)


def test_cross_pool_names_rates_window_and_worker_evidence(env, capsys):
    pools(env)
    task(env, "control")
    task(env, "canary", pool="candidate", state="failed")
    code, result = invoke(env, capsys)
    assert code == 1
    assert (
        result["window"]
        == result["reports"]["baseline"]["window"]
        == result["reports"]["candidate"]["window"]
    )
    assert result["comparison"]["status"] == "comparable"
    assert result["comparison"]["failure_rate_difference_pp"] == 100
    assert result["comparison"]["observed_result"] == "higher candidate failure rate observed"
    assert result["sides"]["candidate"]["requested_hosts"] == 1
    assert result["sides"]["candidate"]["workers"][0]["observed"]["outcomes"]["failed"] == 1
    assert result["sides"]["baseline"]["pool"] == "releng-hardware/talos-pool"
    assert len(result["comparison"]["tasks"]) == 1


def test_many_candidates_match_only_shared_names_and_expose_task_mix(env, capsys):
    pools(env)
    seed(env, "canary2", tc_worker_identity=identity("canary2", pool="candidate"))
    task(env, "control", name="shared")
    task(env, "control", name="baseline-only", state="failed")
    task(env, "canary", pool="candidate", name="shared")
    task(env, "canary2", pool="candidate", name="candidate-only")
    code, result = invoke(env, capsys)
    assert code == 1  # Unmatched observed failures are still failures.
    assert result["comparison"]["status"] == "comparable"
    assert result["comparison"]["baseline"]["finished"] == 1
    assert result["comparison"]["candidate"]["finished"] == 1
    assert result["sides"]["candidate"]["requested_hosts"] == 2
    assert result["sides"]["baseline"]["unmatched_verified"]["outcomes"]["failed"] == 1
    compare.print_comparison(result)
    text = capsys.readouterr().out
    assert "All observed: 1 failed, 0 exception, 0 unknown" in text
    assert text.index("All observed:") < text.index("Window:")


def test_unknown_configuration_failure_remains_visible(env, capsys):
    pools(env)
    seed(
        env,
        "canary",
        early=False,
        puppet_success=False,
        tc_worker_identity=identity("canary", pool="candidate"),
    )
    task(env, "control", minute=110)
    task(env, "canary", pool="candidate", minute=110, state="exception")
    code, result = invoke(env, capsys)
    assert code == 1
    side = result["sides"]["candidate"]
    assert side["observed"]["outcomes"]["exception"] == 1
    assert side["attributed"]["finished"] == 0
    assert side["unattributed"]["outcomes"]["exception"] == 1
    assert result["comparison"]["status"] == "unavailable"


@pytest.mark.parametrize("change", ["role", "name", "sample"])
def test_unmatched_roles_names_and_small_samples_do_not_claim_comparability(env, capsys, change):
    pools(env)
    if change == "role":
        seed(
            env,
            "canary",
            role="other-role",
            puppet_role="other-role",
            tc_worker_identity=identity("canary", pool="candidate"),
        )
    task(env, "control", name="shared")
    task(env, "canary", pool="candidate", name="other" if change == "name" else "shared")
    code, result = invoke(env, capsys, *(["--min-finished", "20"] if change == "sample" else []))
    assert code == 0
    assert result["comparison"]["status"] == "unavailable"


def test_metadata_failure_preserves_task_outcomes_and_attribution(env, capsys):
    pools(env)
    task(env, "control")
    task(env, "canary", pool="candidate")
    env.clients.queue.task.side_effect = RuntimeError("metadata unavailable")
    code, result = invoke(env, capsys)
    assert code == 1
    assert result["comparison"]["status"] == "unavailable"
    for side in result["sides"].values():
        assert side["observed"]["finished"] == side["attributed"]["finished"] == 1
        assert side["task_names"] == {"known": 0, "total": 1}


def test_worker_query_failure_keeps_missing_worker_in_denominator(env, capsys):
    pools(env)
    task(env, "control")
    task(env, "canary", pool="candidate")
    env.clients.worker_manager.getWorker.side_effect = RuntimeError("unavailable")
    code, result = invoke(env, capsys)
    assert code == 1
    for side in result["sides"].values():
        assert side["requested_hosts"] == 1
        assert side["queried_hosts"] == 0
    assert result["comparison"]["status"] == "unavailable"


def test_running_tasks_are_not_finished_successes(env, capsys):
    pools(env)
    task(env, "control")
    task(env, "canary", pool="candidate", state="running", minute=110)
    code, result = invoke(env, capsys)
    assert code == 0
    assert result["sides"]["candidate"]["observed"]["outcomes"]["running"] == 1
    assert result["comparison"]["candidate"]["finished"] == 0
    assert result["comparison"]["status"] == "unavailable"


@pytest.mark.parametrize(
    "extra",
    [
        ["--candidate-pool", "releng-hardware/talos-pool"],
        ["--candidate-pool", "bad/pool/name"],
        ["--workers", "0"],
        ["--metadata-workers", "0"],
        ["--metadata-workers", "21"],
        ["--min-finished", "0"],
        ["--min-coverage", "nan"],
        ["--max-observation-gap", "inf"],
        ["--since", UNTIL.isoformat()],
    ],
)
def test_invalid_input_fails_before_network(env, extra):
    with pytest.raises(SystemExit) as exc:
        compare.parse_args(arguments(env, *extra))
    assert exc.value.code == 2
    env.clients.worker_manager.getWorker.assert_not_called()


def test_missing_pool_membership_is_input_error(env, capsys):
    seed(env, "control")
    assert compare.main(arguments(env)) == 2
    assert "no matching recorded hosts" in capsys.readouterr().err


def test_canonical_cli_and_readable_details(env):
    pools(env)
    task(env, "control")
    task(env, "canary", pool="candidate")
    runner = CliRunner()
    help_result = runner.invoke(cli, ["rollout", "compare", "--help"])
    assert help_result.exit_code == 0
    assert "--candidate-pool" in help_result.output
    result = runner.invoke(cli, ["rollout", "compare", *arguments(env, "--details")])
    assert result.exit_code == 0, result.output
    assert "CANDIDATE" in result.output
    assert "BASELINE" in result.output
    assert "canary:" in result.output
    assert "same observed failure and exception rates" in result.output
    assert "test-benchmark" in result.output


def test_replay_fallback_is_default_and_preserves_original_names(env, capsys):
    pools(env)
    task(env, "control", name="test-benchmark")
    task(env, "canary", pool="candidate", name="Replay: test-benchmark")
    _, exact = invoke(env, capsys, "--exact-task-names")
    assert exact["comparison"]["status"] == "unavailable"
    _, fallback = invoke(env, capsys)
    assert fallback["comparison"]["status"] == "comparable"
    assert fallback["comparison"]["tasks"][0]["match_method"] == "replay_prefix"
    assert fallback["comparison"]["tasks"][0]["candidate_name"] == "Replay: test-benchmark"
    assert fallback["task_matching"]["fallback_matches"] == 1
    assert fallback["sides"]["candidate"]["task_mix"][0]["name"] == "Replay: test-benchmark"
    _, normalized = invoke(env, capsys, "--strip-task-name-prefix", "Replay: ")
    assert normalized["comparison"]["status"] == "comparable"
    mix = normalized["sides"]["candidate"]["task_mix"][0]
    assert mix["name"] == "test-benchmark"
    assert mix["original_names"] == ["Replay: test-benchmark"]
    assert normalized["task_matching"]["strip_prefix"] == "Replay: "


def test_exact_names_take_precedence_without_reusing_tasks(env, capsys):
    pools(env)
    task(env, "control", name="test-benchmark")
    task(env, "control", name="Replay: test-benchmark", state="failed")
    task(env, "canary", pool="candidate", name="Replay: test-benchmark")
    _, result = invoke(env, capsys)
    pair = result["comparison"]["tasks"][0]
    assert pair["match_method"] == "exact"
    assert pair["baseline"]["outcomes"]["failed"] == 1
    assert result["comparison"]["baseline"]["finished"] == 1
    assert result["sides"]["baseline"]["unmatched_verified"]["finished"] == 1
    assert result["task_matching"]["fallback_matches"] == 0


def test_replay_fallback_works_on_baseline_side_and_keeps_roles_separate():
    grouped = {
        "candidate": {("role", "test"): [], ("other-role", "another"): []},
        "baseline": {("role", "Replay: test"): [], ("role", "Replay: another"): []},
    }
    assert compare.match_task_groups(grouped, replay_fallback=True) == [
        (("role", "test"), ("role", "Replay: test"), "replay_prefix")
    ]


def test_replay_fallback_does_not_choose_ambiguous_or_empty_names():
    grouped = {
        "candidate": {("role", "test"): [], ("role", "Replay: test"): [], ("role", "Replay: "): []},
        "baseline": {("role", "Replay: Replay: test"): []},
    }
    # Only one literal prefix is removed; arbitrary/nested prefixes cannot be
    # matched through repeated stripping.
    assert compare.match_task_groups(grouped, replay_fallback=True) == []


def test_partial_host_query_failure_warns_without_vetoing_adequate_coverage(env, capsys):
    pools(env)
    for number in range(9):
        seed(env, f"control-{number}")
        task(env, f"control-{number}")
    task(env, "canary", pool="candidate")
    get_worker = env.clients.worker_manager.getWorker.side_effect

    def fetch(provisioner, pool, group, worker):
        if worker == "control":
            raise RuntimeError("worker query failed")
        return get_worker(provisioner, pool, group, worker)

    env.clients.worker_manager.getWorker.side_effect = fetch
    code, result = invoke(env, capsys)
    assert code == 1
    assert result["sides"]["baseline"]["queried_hosts"] == 9
    assert result["sides"]["baseline"]["requested_hosts"] == 10
    assert result["comparison"]["status"] == "comparable"


def test_same_pool_name_under_different_provisioners_is_distinct(env):
    parsed = compare.parse_args(arguments(env, "--candidate-pool", "other/talos-pool"))
    assert parsed.candidate_pool == ("other", "talos-pool")


def test_task_identity_mismatch_is_reported_and_cannot_enter_rates(env, capsys):
    pools(env)
    task(env, "control")
    task_id = task(env, "canary", pool="candidate")
    env.tasks[task_id][1]["status"]["runs"][0]["workerId"] = "different-worker"
    code, result = invoke(env, capsys)
    assert code == 1
    assert result["comparison"]["status"] == "unavailable"
    assert result["comparison"]["candidate"]["finished"] == 0


@pytest.mark.parametrize("json_output", [False, True])
def test_phase_progress_is_on_stderr_and_json_mode_is_silent(env, capsys, monkeypatch, json_output):
    pools(env)
    task(env, "control")
    task(env, "canary", pool="candidate")
    terminal = Terminal()
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setattr(compare.sys, "stderr", terminal)
    assert compare.main(arguments(env, *(["--json"] if json_output else []))) == 0
    output = capsys.readouterr().out
    if json_output:
        assert json.loads(output)["schema_version"] == 1
        assert terminal.getvalue() == ""
    else:
        assert "Candidate: Processing workers and task outcomes 1/1" in terminal.getvalue()
        assert "Baseline: Processing workers and task outcomes 1/1" in terminal.getvalue()
        assert "1 successful, 0 errors" in terminal.getvalue()
        assert "Fetching task names 2/2; 0 cached, 0 reused, 0 errors" in terminal.getvalue()
        assert "Matching workloads and comparing outcomes" in terminal.getvalue()
        assert "Fetching task names" not in output


def test_warm_cache_skips_metadata_but_refreshes_outcomes_and_fetches_new_tasks(env, capsys):
    pools(env)
    task(env, "control")
    canary = task(env, "canary", pool="candidate")
    _, cold = invoke(env, capsys)
    assert cold["task_name_lookups"]["fetched"] == 2
    env.clients.queue.task.reset_mock()
    env.clients.queue.status.reset_mock()
    env.tasks[canary][1]["status"]["runs"][0]["state"] = "running"
    _, warm = invoke(env, capsys)
    env.clients.queue.task.assert_not_called()
    assert env.clients.queue.status.call_count == 2
    assert warm["task_name_lookups"]["cached"] == 2
    assert warm["sides"]["candidate"]["observed"]["outcomes"]["running"] == 1
    new = task(env, "control", name="new workload")
    _, updated = invoke(env, capsys)
    env.clients.queue.task.assert_called_once_with(new)
    assert updated["task_name_lookups"]["cached"] == 2
    assert updated["task_name_lookups"]["fetched"] == 1


def test_reuses_report_metadata_for_failed_jobs(env, capsys):
    pools(env)
    task(env, "control", state="failed")
    task(env, "canary", pool="candidate", state="exception")
    _, result = invoke(env, capsys)
    assert env.clients.queue.task.call_count == 2
    assert result["task_name_lookups"]["reused"] == 2
    assert result["task_name_lookups"]["fetched"] == 0


def test_failed_name_lookups_are_retried_and_not_cached(env, capsys):
    pools(env)
    task(env, "control")
    task(env, "canary", pool="candidate")
    fetch = env.clients.queue.task.side_effect
    env.clients.queue.task.side_effect = RuntimeError("metadata unavailable")
    code, failed = invoke(env, capsys)
    assert code == 1
    assert failed["task_name_lookups"]["failed"] == 2
    env.clients.queue.task.side_effect = fetch
    code, recovered = invoke(env, capsys)
    assert code == 0
    assert recovered["task_name_lookups"]["cached"] == 0
    assert recovered["task_name_lookups"]["fetched"] == 2


def test_cache_bypass_and_unavailable_cache_preserve_comparison(env, capsys):
    pools(env)
    task(env, "control")
    task(env, "canary", pool="candidate")
    path = env.db.with_suffix(".task-names.sqlite3")
    code, bypassed = invoke(env, capsys, "--no-task-name-cache")
    assert code == 0
    assert not path.exists()
    assert bypassed["task_name_lookups"]["fetched"] == 2
    path.write_text("invalid SQLite file")
    code, result = invoke(env, capsys)
    assert code == 0
    assert result["comparison"] == bypassed["comparison"]
    assert any("Task-name cache read unavailable" in warning for warning in result["warnings"])
    assert result["task_name_lookups"]["fetched"] == 2


@pytest.mark.parametrize("minimum", [1, 20])
def test_text_leads_with_assessment_blockers_and_sample_counts(env, capsys, minimum):
    pools(env)
    task(env, "control")
    task(env, "canary", pool="candidate")
    _, result = invoke(env, capsys, "--min-finished", str(minimum))
    compare.print_comparison(result)
    text = capsys.readouterr().out
    assert text.index("MATCHED VERIFIED COMPARISON:") < text.index("Window:")
    assert "Candidate: 1 matched finished" in text
    assert "Baseline: 1 matched finished" in text
    assert "attribution 100.0%" in text
    if minimum == 20:
        assert text.index("Blocker: insufficient finished sample") < text.index("CANDIDATE:")
        assert "minimum 20 per side" in text
        assert "difference pp" not in text
    else:
        assert "same observed failure and exception rates" in text
        assert "failure rate difference pp (candidate - baseline): +0.00" in text
    assert "Observed: 1 completed" in text
    assert "0 pending" not in text


def test_worker_errors_group_by_side_without_losing_full_diagnostics(env, capsys):
    pools(env)
    for number in range(4):
        seed(env, f"control-{number}")
    task(env, "canary", pool="candidate")
    fetch = env.clients.worker_manager.getWorker.side_effect

    def get_worker(provisioner, pool, group, worker):
        if worker.startswith("control"):
            raise RuntimeError(
                f"Worker {worker} not found.\n\n---\n\n* method: getWorker\n"
                "* errorCode: ResourceNotFound\n* statusCode: 404\n* time: test-timestamp"
            )
        return fetch(provisioner, pool, group, worker)

    env.clients.worker_manager.getWorker.side_effect = get_worker
    code, result = invoke(env, capsys)
    assert code == 1
    assert len(result["errors"]) == 5
    assert all("* method: getWorker" in error for error in result["errors"])
    compare.print_comparison(result)
    text = " ".join(capsys.readouterr().out.split())
    assert "baseline: 5 worker(s): Taskcluster worker not found (ResourceNotFound)" in text
    assert "+2 more" in text
    assert "* method:" not in text
    assert "--verbose or --json for full errors" in text
    compare.print_comparison(result, details=True)
    text = capsys.readouterr().out
    assert "control-3:" in text
    assert "* method:" not in text
    compare.print_comparison(result, verbose=True)
    assert "* method: getWorker" in capsys.readouterr().out


def test_grouping_keeps_distinct_worker_errors_and_nonworker_errors_separate():
    result = {
        "sides": {
            "candidate": {"workers": [{"host": "a", "error": "observation failed"}]},
            "baseline": {
                "workers": [
                    {"host": "b", "error": "observation failed"},
                    {"host": "c", "error": "permission denied"},
                ]
            },
        },
        "errors": [
            "candidate: a: observation failed",
            "baseline: b: observation failed",
            "baseline: c: permission denied",
            "baseline: task query unavailable",
        ],
    }
    errors = compare.comparison_errors(result)
    assert len(errors) == 4
    assert errors[0] == "candidate: 1 worker(s): observation failed (a)"
    assert errors[-1] == "baseline: task query unavailable"
    assert compare.comparison_errors(result, verbose=True) == result["errors"]
