"""Regression coverage for observed TC identity and partial collections."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from fleetroll.cli import cli
from fleetroll.commands import gather_tc
from fleetroll.db import get_latest_tc_workers, insert_host_observation, insert_tc_worker
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
