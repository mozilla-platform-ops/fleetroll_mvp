"""Regression coverage for observed TC identity and partial collections."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from fleetroll.cli import cli
from fleetroll.commands import gather_tc
from fleetroll.db import (
    get_latest_tc_lookup_attempts,
    get_latest_tc_workers,
    insert_host_observation,
    insert_tc_worker,
)
from fleetroll.exceptions import FleetRollError
from fleetroll.taskcluster import WorkerFetchResult

IDENTITY = {
    "provisionerId": "releng-hardware",
    "workerType": "gecko-t-linux-talos-2404-wayland",
    "workerGroup": "mdc1",
    "workerId": "not-the-hostname",
}
OLD_TS = "2026-10-30T00:00:00Z"
NEW_TS = "2026-11-04T00:00:00Z"


@pytest.fixture
def tc_env(monkeypatch, tmp_path, temp_db, db_conn):
    monkeypatch.setattr("fleetroll.db.get_db_path", lambda: temp_db)
    monkeypatch.setattr(gather_tc, "load_tc_credentials", Mock())
    monkeypatch.setattr(gather_tc, "create_tc_clients", Mock())
    monkeypatch.setattr(gather_tc, "utc_now_iso", lambda: NEW_TS)
    monkeypatch.setattr(gather_tc, "default_audit_log_path", lambda: tmp_path / "audit.jsonl")
    monkeypatch.setattr("fleetroll.github.do_github_fetch", Mock())
    fetch = Mock(return_value=WorkerFetchResult(workers=[], status_errors=0))
    monkeypatch.setattr(gather_tc, "fetch_workers", fetch)
    return fetch


def seed(db_conn, host, *, identity=IDENTITY, status="ok", legacy=False, role="new_unknown_role"):
    observed = {"role": role}
    if not legacy:
        observed.update(tc_worker_identity=identity, tc_worker_identity_status=status)
    insert_host_observation(
        db_conn,
        {
            "host": host,
            "ts": NEW_TS,
            "ok": True,
            "observed": observed,
        },
    )
    insert_tc_worker(
        db_conn,
        {
            "host": host,
            "ts": OLD_TS,
            "worker_type": "old-pool",
            "type": "worker",
        },
    )
    db_conn.commit()


def worker(identity=IDENTITY, **extra):
    return {
        "workerId": identity["workerId"],
        "workerGroup": identity["workerGroup"],
        "lastDateActive": NEW_TS,
        **extra,
    }


def invoke(hosts, *, tmp_path, quiet=False):
    host_file = tmp_path / "hosts.list"
    host_file.write_text("\n".join(hosts) + "\n")
    return CliRunner().invoke(cli, ["gather-tc", str(host_file), *(["-q"] if quiet else ["-v"])])


@pytest.mark.parametrize("quiet", [False, True])
def test_unknown_role_uses_configured_pool_and_full_identity(tc_env, db_conn, tmp_path, quiet):
    seed(db_conn, "host.example.com")
    tc_env.return_value = WorkerFetchResult(
        workers=[worker({**IDENTITY, "workerGroup": "wrong-group"}), worker()],
        status_errors=0,
    )
    result = invoke(["host.example.com"], tmp_path=tmp_path, quiet=quiet)
    assert result.exit_code == 0, result.output
    assert tc_env.call_args.args == ("releng-hardware",)
    assert tc_env.call_args.kwargs["worker_type"] == IDENTITY["workerType"]
    assert tc_env.call_args.kwargs["worker_ids"] == set()
    assert tc_env.call_args.kwargs["worker_identities"] == {("mdc1", "not-the-hostname")}
    record = get_latest_tc_workers(db_conn, ["host.example.com"])["host.example.com"]
    assert record["ts"] == NEW_TS
    assert record["worker_id"] == "not-the-hostname"
    assert record["worker_group"] == "mdc1"
    assert record["worker_type"] == IDENTITY["workerType"]
    attempt = get_latest_tc_lookup_attempts(db_conn, ["host.example.com"])["host.example.com"]
    assert attempt["status"] == "success"
    assert attempt["successful_observation_ts"] == NEW_TS
    assert attempt["identity"] == {
        "provisioner": IDENTITY["provisionerId"],
        "worker_type": IDENTITY["workerType"],
        "worker_group": IDENTITY["workerGroup"],
        "worker_id": IDENTITY["workerId"],
    }
    assert attempt["resolution_source"] == "host_configuration"
    assert "legacy" not in result.output


@pytest.mark.parametrize("quiet", [False, True])
@pytest.mark.parametrize("status", ["missing", "unreadable", "invalid", "ambiguous"])
def test_explicit_identity_failure_blocks_legacy_fallback(tc_env, db_conn, tmp_path, quiet, status):
    seed(db_conn, "host", identity=None, status=status, role="gecko_t_linux_talos")
    result = invoke(["host"], tmp_path=tmp_path, quiet=quiet)
    assert result.exit_code != 0
    if quiet:
        assert "TC 0/1 collected" in result.output
        assert "host:" not in result.output
        assert len(result.output.splitlines()) == 1
    else:
        assert "host: worker identity unresolved" in result.output
    tc_env.assert_not_called()
    assert get_latest_tc_workers(db_conn, ["host"])["host"]["ts"] == OLD_TS
    attempt = get_latest_tc_lookup_attempts(db_conn, ["host"])["host"]
    assert attempt["status"] == "identity_unresolved"
    assert all(value is None for value in attempt["identity"].values())


@pytest.mark.parametrize("quiet", [False, True])
def test_legacy_observation_can_still_collect(tc_env, db_conn, tmp_path, quiet):
    seed(db_conn, "host.example.com", legacy=True, role="gecko_t_linux_talos")
    tc_env.return_value = WorkerFetchResult(
        workers=[worker({**IDENTITY, "workerId": "host"})],
        status_errors=0,
    )
    result = invoke(["host.example.com"], tmp_path=tmp_path, quiet=quiet)
    assert result.exit_code == 0, result.output
    assert ("WARNING" if quiet else "legacy role/override") in result.output
    if quiet:
        assert "host.example.com" not in result.output
        assert len(result.output.splitlines()) == 1
    assert tc_env.call_args.kwargs["worker_type"] == "gecko-t-linux-talos-1804"


@pytest.mark.parametrize("quiet", [False, True])
@pytest.mark.parametrize("failure", ["unresolved", "missing", "wrong-group", "api", "task-status"])
def test_partial_success_preserves_failed_host_timestamp(tc_env, db_conn, tmp_path, quiet, failure):
    seed(db_conn, "good")
    bad_identity = {**IDENTITY, "workerType": "bad-pool", "workerId": "bad-id"}
    seed(
        db_conn, "bad", identity=bad_identity, status="missing" if failure == "unresolved" else "ok"
    )

    def fetch(provisioner, *, worker_type, **kwargs):
        if worker_type == IDENTITY["workerType"]:
            return WorkerFetchResult(workers=[worker()], status_errors=0)
        if failure == "api":
            raise FleetRollError("test API failure")
        if failure == "task-status":
            return WorkerFetchResult(
                workers=[
                    worker(
                        bad_identity,
                        latestTask={
                            "taskId": "test-task",
                            "runId": 0,
                        },
                    )
                ],
                status_errors=1,
            )
        if failure == "wrong-group":
            return WorkerFetchResult(
                workers=[worker({**bad_identity, "workerGroup": "other"})], status_errors=0
            )
        return WorkerFetchResult(workers=[], status_errors=0)

    tc_env.side_effect = fetch
    result = invoke(["good", "bad"], tmp_path=tmp_path, quiet=quiet)
    assert result.exit_code != 0
    if quiet:
        assert "TC 1/2 collected" in result.output
        assert "bad" not in result.output
        assert "FAILED" in result.output
        assert len(result.output.splitlines()) == 1
    else:
        assert "bad" in result.output
    records = get_latest_tc_workers(db_conn, ["good", "bad"])
    assert records["good"]["ts"] == NEW_TS
    assert records["bad"]["ts"] == OLD_TS
    attempts = get_latest_tc_lookup_attempts(db_conn, ["good", "bad"])
    assert attempts["good"]["status"] == "success"
    assert (
        attempts["bad"]["status"]
        == {
            "unresolved": "identity_unresolved",
            "missing": "worker_missing",
            "wrong-group": "worker_missing",
            "api": "pool_query_failed",
            "task-status": "task_lookup_failed",
        }[failure]
    )
    assert attempts["bad"]["ts"] == NEW_TS
    assert attempts["bad"]["successful_observation_ts"] is None


@pytest.mark.parametrize("quiet", [False, True])
def test_override_mismatch_uses_observed_pool(tc_env, db_conn, tmp_path, monkeypatch, quiet):
    seed(db_conn, "host")
    monkeypatch.setattr(
        gather_tc, "build_host_override_pools", lambda *args: {"host": "intended-pool"}
    )
    tc_env.return_value = WorkerFetchResult(workers=[worker()], status_errors=0)
    result = invoke(["host"], tmp_path=tmp_path, quiet=quiet)
    assert result.exit_code == 0, result.output
    assert (
        "WARNING" if quiet else "differs from WORKER_TYPE_OVERRIDE=intended-pool"
    ) in result.output
    if quiet:
        assert "intended-pool" not in result.output
        assert len(result.output.splitlines()) == 1
    assert tc_env.call_args.kwargs["worker_type"] == IDENTITY["workerType"]


def test_legacy_duplicate_worker_ids_are_not_arbitrarily_matched(tc_env, db_conn, tmp_path):
    seed(db_conn, "host.example.com", legacy=True, role="gecko_t_linux_talos")
    tc_env.return_value = WorkerFetchResult(
        workers=[
            worker({**IDENTITY, "workerId": "host"}),
            worker({**IDENTITY, "workerId": "host", "workerGroup": "elsewhere"}),
        ],
        status_errors=0,
    )
    result = invoke(["host.example.com"], tmp_path=tmp_path)
    assert result.exit_code != 0
    assert get_latest_tc_workers(db_conn, ["host.example.com"])["host.example.com"]["ts"] == OLD_TS
    attempt = get_latest_tc_lookup_attempts(db_conn, ["host.example.com"])["host.example.com"]
    assert attempt["status"] == "worker_ambiguous"
    assert attempt["resolution_source"] == "legacy_role"
    assert (
        "host.example.com: multiple workers match host; skipping ambiguous match" in result.output
    )
    assert "1 ambiguous worker match(es)" in result.output


def test_ambiguous_worker_match_is_quiet_in_quiet_mode(tc_env, db_conn, tmp_path):
    seed(db_conn, "host.example.com", legacy=True, role="gecko_t_linux_talos")
    tc_env.return_value = WorkerFetchResult(
        workers=[
            worker({**IDENTITY, "workerId": "host"}),
            worker({**IDENTITY, "workerId": "host", "workerGroup": "elsewhere"}),
        ],
        status_errors=0,
    )
    result = invoke(["host.example.com"], tmp_path=tmp_path, quiet=True)
    assert "multiple workers match" not in result.output


def test_failed_windows_discovery_does_not_discard_observed_linux_success(
    tc_env,
    db_conn,
    tmp_path,
    monkeypatch,
):
    seed(db_conn, "good")
    seed(db_conn, "windows", legacy=True, role="win116424h2hw")
    monkeypatch.setattr(
        gather_tc,
        "build_windows_role_mapping",
        Mock(
            side_effect=FleetRollError("test discovery failure"),
        ),
    )
    tc_env.return_value = WorkerFetchResult(workers=[worker()], status_errors=0)
    result = invoke(["good", "windows"], tmp_path=tmp_path, quiet=True)
    assert result.exit_code != 0
    assert "FAILED TC 1/2 collected" in result.output
    assert "test discovery failure" not in result.output
    assert len(result.output.splitlines()) == 1
    records = get_latest_tc_workers(db_conn, ["good", "windows"])
    assert records["good"]["ts"] == NEW_TS
    assert records["windows"]["ts"] == OLD_TS


@pytest.mark.parametrize("quiet", [False, True])
def test_large_unresolved_population_has_bounded_output(tc_env, db_conn, tmp_path, quiet):
    hosts = [f"unresolved-{number}.example.com" for number in range(30)]
    for host in hosts:
        seed(db_conn, host, identity=None, status="missing")
    host_file = tmp_path / "hosts.list"
    host_file.write_text("\n".join(hosts) + "\n")
    result = CliRunner().invoke(cli, ["gather-tc", str(host_file), *(["-q"] if quiet else [])])
    assert result.exit_code != 0
    assert ("TC 0/30 collected" if quiet else "30 unresolved identities") in result.output
    assert "-v for details" in result.output
    assert not any(host in result.output for host in hosts)
    if quiet:
        assert len(result.output.splitlines()) == 1
    tc_env.assert_not_called()


def test_all_pool_requests_failed_still_prints_one_quiet_summary(tc_env, db_conn, tmp_path):
    seed(db_conn, "host")
    tc_env.side_effect = FleetRollError("long private API diagnostic")
    result = invoke(["host"], tmp_path=tmp_path, quiet=True)
    assert result.exit_code != 0
    assert "FAILED" in result.output
    assert "TC 0/1 collected" in result.output
    assert "long private API diagnostic" not in result.output
    assert len(result.output.splitlines()) == 1
    assert get_latest_tc_workers(db_conn, ["host"])["host"]["ts"] == OLD_TS


def test_pool_failure_fans_out_and_does_not_store_private_errors(tc_env, db_conn, tmp_path):
    seed(db_conn, "first")
    seed(db_conn, "second", identity={**IDENTITY, "workerId": "second-id"})
    tc_env.side_effect = FleetRollError("secret-in-a-private-url-or-token")
    assert invoke(["first", "second"], tmp_path=tmp_path).exit_code != 0
    attempts = get_latest_tc_lookup_attempts(db_conn, ["first", "second"])
    assert set(attempts) == {"first", "second"}
    assert all(record["status"] == "pool_query_failed" for record in attempts.values())
    assert "secret-in-a-private-url-or-token" not in str(attempts)
    assert all(
        record["ts"] == OLD_TS for record in get_latest_tc_workers(db_conn, list(attempts)).values()
    )


@pytest.mark.parametrize("failure", ["missing", "api", "task", "unresolved"])
def test_success_then_failure_preserves_success_and_tracks_changed_pool(
    tc_env, db_conn, tmp_path, monkeypatch, failure
):
    seed(db_conn, "host")
    tc_env.return_value = WorkerFetchResult(workers=[worker()], status_errors=0)
    assert invoke(["host"], tmp_path=tmp_path).exit_code == 0
    successful = get_latest_tc_workers(db_conn, ["host"])["host"]
    later_ts = "2026-11-05T00:00:00Z"
    monkeypatch.setattr(gather_tc, "utc_now_iso", lambda: later_ts)
    changed_identity = {**IDENTITY, "workerType": "new-pool", "workerId": "new-id"}
    insert_host_observation(
        db_conn,
        {
            "host": "host",
            "ts": later_ts,
            "ok": True,
            "observed": {
                "tc_worker_identity_status": "missing" if failure == "unresolved" else "ok",
                "tc_worker_identity": changed_identity,
            },
        },
    )
    db_conn.commit()
    if failure == "api":
        tc_env.side_effect = FleetRollError("test failure")
    else:
        tc_env.return_value = WorkerFetchResult(
            workers=[worker(changed_identity, latestTask={"taskId": "task", "runId": 0})]
            if failure == "task"
            else [],
            status_errors=1 if failure == "task" else 0,
        )
    assert invoke(["host"], tmp_path=tmp_path).exit_code != 0
    assert get_latest_tc_workers(db_conn, ["host"])["host"] == successful
    attempt = get_latest_tc_lookup_attempts(db_conn, ["host"])["host"]
    assert attempt["ts"] == later_ts
    assert attempt["status"] != "success"
    assert attempt["identity"]["worker_type"] == (None if failure == "unresolved" else "new-pool")


def test_failure_without_previous_success_is_stored(tc_env, db_conn, tmp_path):
    assert invoke(["never-seen"], tmp_path=tmp_path).exit_code != 0
    assert get_latest_tc_workers(db_conn, ["never-seen"]) == {}
    assert (
        get_latest_tc_lookup_attempts(db_conn, ["never-seen"])["never-seen"]["status"]
        == "identity_unresolved"
    )


def test_failed_audit_blocks_legacy_old_pool(tc_env, db_conn, tmp_path):
    seed(db_conn, "host", legacy=True, role="gecko_t_linux_talos")
    insert_host_observation(db_conn, {"host": "host", "ts": "2026-11-05T00:00:00Z", "ok": False})
    db_conn.commit()
    assert invoke(["host"], tmp_path=tmp_path).exit_code != 0
    tc_env.assert_not_called()
    assert (
        get_latest_tc_lookup_attempts(db_conn, ["host"])["host"]["status"] == "identity_unresolved"
    )


def test_failed_audit_does_not_reuse_retained_complete_identity(tc_env, db_conn, tmp_path):
    seed(db_conn, "host")
    insert_host_observation(db_conn, {"host": "host", "ts": "2026-11-05T00:00:00Z", "ok": False})
    db_conn.commit()
    assert gather_tc.get_observed_identities(["host"], db_conn=db_conn) == {"host": None}
    assert invoke(["host"], tmp_path=tmp_path).exit_code != 0
    tc_env.assert_not_called()
    attempt = get_latest_tc_lookup_attempts(db_conn, ["host"])["host"]
    assert attempt["status"] == "identity_unresolved"
    assert attempt["identity"]["worker_id"] is None


@pytest.mark.parametrize("failure", [None, "missing", "api", "task", "ambiguous"])
def test_historical_lookup_while_ssh_fails(tc_env, db_conn, failure):
    seed(db_conn, "host.example.com")
    insert_host_observation(
        db_conn, {"host": "host.example.com", "ts": "2026-11-05T00:00:00Z", "ok": False}
    )
    db_conn.commit()
    if failure == "api":
        tc_env.side_effect = FleetRollError("private-error")
    else:
        workers = [] if failure == "missing" else [worker()]
        if failure == "task":
            workers = [worker(latestTask={"taskId": "test-task", "runId": 0})]
        if failure == "ambiguous":
            workers = [worker(), worker()]
        tc_env.return_value = WorkerFetchResult(
            workers=workers, status_errors=int(failure == "task")
        )
    result = CliRunner().invoke(
        cli, ["gather", "tc", "--use-historical-identity", "host.example.com", "-v"]
    )
    assert (result.exit_code == 0) == (failure is None), result.output
    assert tc_env.call_args.kwargs["worker_ids"] == set()
    assert tc_env.call_args.kwargs["worker_identities"] == {("mdc1", "not-the-hostname")}
    assert tc_env.call_args.kwargs["worker_type"] == IDENTITY["workerType"]
    attempt = get_latest_tc_lookup_attempts(db_conn, ["host.example.com"])["host.example.com"]
    assert attempt["resolution_source"] == "historical_worker_identity"
    assert attempt["historical_baseline"]["observation_ts"] == NEW_TS
    assert (
        attempt["status"]
        == {
            None: "success",
            "missing": "worker_missing",
            "api": "pool_query_failed",
            "task": "task_lookup_failed",
            "ambiguous": "worker_ambiguous",
        }[failure]
    )
    successful = get_latest_tc_workers(db_conn, ["host.example.com"])["host.example.com"]
    if failure is None:
        assert successful["historical_baseline"] == attempt["historical_baseline"]
        assert attempt["successful_observation_ts"] == successful["ts"]
    else:
        assert successful["ts"] == OLD_TS
        assert attempt["successful_observation_ts"] is None


def test_historical_mode_never_falls_back_for_missing_baseline(tc_env, db_conn):
    seed(db_conn, "host", legacy=True, role="gecko_t_linux_talos")
    result = CliRunner().invoke(cli, ["gather", "tc", "host", "--use-historical-identity", "-q"])
    assert result.exit_code != 0
    tc_env.assert_not_called()
    attempt = get_latest_tc_lookup_attempts(db_conn, ["host"])["host"]
    assert attempt["status"] == "identity_unresolved"
    assert attempt["resolution_source"] == "historical_worker_identity"
    assert attempt["historical_baseline"] is None


def test_historical_query_pins_baseline_during_concurrent_replacement(tc_env, db_conn):
    from datetime import UTC, datetime

    from fleetroll.commands.monitor.cache import ShaInfoCache
    from fleetroll.worker_history import load_history, render_history

    seed(db_conn, "host")
    replacement = {**IDENTITY, "workerGroup": "new-group", "workerId": "new-id"}

    def fetch(*args, **kwargs):
        insert_host_observation(
            db_conn,
            {
                "host": "host",
                "ts": "2026-11-05T00:00:00Z",
                "ok": True,
                "observed": {"tc_worker_identity_status": "ok", "tc_worker_identity": replacement},
            },
        )
        db_conn.commit()
        return WorkerFetchResult(workers=[worker()], status_errors=0)

    tc_env.side_effect = fetch
    result = CliRunner().invoke(cli, ["gather", "tc", "host", "--use-historical-identity"])
    assert result.exit_code == 0, result.output
    attempt = get_latest_tc_lookup_attempts(db_conn, ["host"])["host"]
    assert attempt["identity"]["worker_id"] == IDENTITY["workerId"]
    assert attempt["historical_baseline"]["observation_ts"] == NEW_TS
    path = Path(db_conn.execute("PRAGMA database_list").fetchone()[2]).parent
    current = render_history(
        load_history(db_conn, hosts=["host"])["host"],
        sha_cache=ShaInfoCache(path / "overrides", path / "vault"),
        now=datetime.now(UTC),
    )
    assert current["worker_id"] == "new-id"
    assert current["provenance"] != attempt["historical_baseline"]


def test_historical_success_does_not_clear_override_block(tc_env, db_conn):
    from fleetroll.worker_history import load_history, record_write

    seed(db_conn, "host")
    record_write(db_conn, record={"host": "host", "action": "host.set_override", "ts": NEW_TS})
    db_conn.commit()
    before = load_history(db_conn, hosts=["host"])
    tc_env.return_value = WorkerFetchResult(workers=[worker()], status_errors=0)
    result = CliRunner().invoke(cli, ["gather", "tc", "host", "--use-historical-identity", "-v"])
    assert result.exit_code == 0, result.output
    assert "contradiction=contradicted" in result.output
    assert load_history(db_conn, hosts=["host"]) == before


def test_historical_uncertain_is_not_counted_as_pool_mismatch(tc_env, db_conn):
    seed(db_conn, "host")
    tc_env.return_value = WorkerFetchResult(workers=[worker()], status_errors=0)
    result = CliRunner().invoke(cli, ["gather", "tc", "host", "--use-historical-identity", "-v"])
    assert result.exit_code == 0, result.output
    assert "contradiction=uncertain" in result.output
    assert "1 uncertain historical identit(ies)" in result.output
    assert "pool mismatch" not in result.output


def test_historical_contradicted_is_counted_as_pool_mismatch(tc_env, db_conn):
    from fleetroll.worker_history import record_write

    seed(db_conn, "host")
    record_write(db_conn, record={"host": "host", "action": "host.set_override", "ts": NEW_TS})
    db_conn.commit()
    tc_env.return_value = WorkerFetchResult(workers=[worker()], status_errors=0)
    result = CliRunner().invoke(cli, ["gather", "tc", "host", "--use-historical-identity", "-v"])
    assert result.exit_code == 0, result.output
    assert "1 pool mismatch(es)" in result.output
    assert "uncertain historical" not in result.output
