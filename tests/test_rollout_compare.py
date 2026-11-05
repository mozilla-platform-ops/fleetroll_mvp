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
        assert "Candidate: Checking workers and task outcomes 1/1" in terminal.getvalue()
        assert "Baseline: Checking workers and task outcomes 1/1" in terminal.getvalue()
        assert "Fetching task names for workload matching 2/2" in terminal.getvalue()
        assert "Matching workloads and comparing outcomes" in terminal.getvalue()
        assert "Fetching task names" not in output
