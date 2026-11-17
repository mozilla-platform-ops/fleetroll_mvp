"""Recorded diagnostics retain provenance and fail unknown independently of host state."""

import base64
import copy
import json
import os
import shlex
import shutil
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
from fleetroll.audit import process_audit_result
from fleetroll.commands.monitor.data import build_row_values, diagnostic_row_values
from fleetroll.commands.monitor.query import apply_query, parse_query, validate_query
from fleetroll.db import get_connection, get_latest_host_observations, init_db
from fleetroll.gwhc import parse_recorded_gwhc, recorded_signals
from fleetroll.ssh import audit_script_body, recorded_gwhc_script_body
from fleetroll.utils import parse_kv_lines

from tests.tmux_helpers import _UV_PATH, TmuxSession, skip_no_tmux


def health(*, process="S", free=20, pct=50):
    pids = [123] if process and process not in ("Z", "X") else []
    failed = process in (None, "Z", "X") or free < 5 or (pct is not None and pct < 10)
    disk = {"free_gb": free}
    if pct is not None:
        disk["free_pct"] = pct
    return {
        "schema_version": 1,
        "host": "worker.example.com",
        "ok": not failed,
        "state": "DEGRADED" if failed else "IDLE",
        "ts": datetime.now(UTC).isoformat(),
        "checks": [
            {
                "name": "generic_worker_process",
                "status": "fail" if process in (None, "Z", "X") else "pass",
                "detail": "test process",
                "data": {
                    "generic_worker_pids": pids,
                    "zombie_pids": [123] if process == "Z" else [],
                    "processes": [{"pid": 123, "ppid": 1, "state": process}] if process else [],
                },
            },
            {
                "name": "disk_free",
                "status": "fail" if free < 5 or (pct is not None and pct < 10) else "pass",
                "detail": "test disks",
                "data": {"/": disk},
            },
        ],
    }


def transport(report=None, **overrides):
    report = report or health()
    return {
        "OS_TYPE": "Linux",
        "GWHC_CHECKED_AT": report["ts"],
        "GWHC_EXIT": "0" if report["ok"] else "1",
        "GWHC_STDOUT_B64": base64.b64encode(json.dumps(report).encode()).decode(),
        **overrides,
    }


def record(report=None, **overrides):
    return {
        "ok": True,
        "ts": datetime.now(UTC).isoformat(),
        "observed": {
            "os_type": "Linux",
            "role": "test_role",
            "role_present": True,
            "gwhc": parse_recorded_gwhc(transport(report, **overrides), ssh_rc=0),
        },
    }


@pytest.mark.parametrize(
    ("process", "expected"),
    [("S", "running"), ("D", "running"), (None, "stopped"), ("Z", "stopped"), ("X", "stopped")],
)
def test_worker_uses_processes_independently_of_aggregate_health(process, expected):
    rec = record(health(process=process, free=1))
    signals = recorded_signals(rec)
    assert signals["worker_status"] == expected
    assert signals["disk_status"] == "alert"
    assert rec["observed"]["gwhc"]["health"] == "unhealthy"
    # Neither a runner process nor TC activity supplies generic-worker state.
    row = build_row_values("worker.example.com", rec, tc_data={"last_date_active": rec["ts"]})
    assert row["gwhc_worker"] == expected


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "bad_pids", "unverified", "contradictory"]
)
def test_incomplete_worker_evidence_is_unknown(change):
    report = health(process=None)
    check = report["checks"][0]
    if change == "missing":
        check["name"] = "worker_runner_process"
    elif change == "duplicate":
        report["checks"].append(copy.deepcopy(check))
    elif change == "bad_pids":
        check["data"]["generic_worker_pids"] = [True]
    elif change == "unverified":
        check["data"]["unverified_pids"] = [123]
    else:
        check["data"]["generic_worker_pids"] = [123]
    assert recorded_signals(record(report))["worker_status"] == "unknown"


@pytest.mark.parametrize(
    ("free", "pct", "status"),
    [(5, 10, "ok"), (4.9, 50, "alert"), (20, 9.9, "alert"), (20, None, "ok"), (0, None, "alert")],
)
def test_disk_thresholds_units_and_absolute_only(free, pct, status):
    rec = record(health(free=free, pct=pct))
    signals = recorded_signals(rec)
    assert signals["disk_status"] == status
    assert signals["disks"][0]["free_gib"] == free
    assert signals["disks"][0]["free_pct"] == pct
    row = diagnostic_row_values(rec)
    assert f"/:{free:g}GiB" in row["gwhc_disk"]
    assert ("%" in row["gwhc_disk"]) == (pct is not None)


@pytest.mark.parametrize(
    "values",
    [
        {"error": "unreadable"},
        {},
        {"free_gb": -1},
        {"free_gb": True},
        {"free_gb": float("nan")},
        {"free_gb": 10, "free_pct": 101},
        {"free_gb": 10, "total_gb": 5},
    ],
)
def test_invalid_disk_metrics_are_unknown(values):
    report = health()
    report["checks"][1]["data"]["/home"] = values
    signals = recorded_signals(record(report))
    assert signals["disk_status"] == "unknown"
    assert signals["disks"][1]["path"] == "/home"
    assert signals["disks"][1]["free_gib"] is None


def test_alert_survives_partial_disk_inspection_and_cleaner():
    report = health(free=1)
    report["checks"][1]["data"].update({"/home": {"error": "unreadable"}, "cleaner_running": True})
    signals = recorded_signals(record(report))
    assert signals["disk_status"] == "alert"
    assert len(signals["disks"]) == 2


@pytest.mark.parametrize(
    ("age", "freshness"), [(899, "fresh"), (900, "stale"), (1800, "stale"), (-120, "unknown")]
)
def test_oldest_source_timestamp_controls_freshness(age, freshness):
    now = datetime.now(UTC)
    rec = record()
    rec["observed"]["gwhc"]["gwhc_ts"] = (now - timedelta(seconds=age)).isoformat()
    signals = recorded_signals(rec, now=now)
    assert signals["freshness"] == freshness
    assert signals["worker_status"] == ("running" if freshness == "fresh" else freshness)


def test_failed_latest_gather_cannot_show_fresh_retained_running():
    retained = record()
    failed = {"ok": False, "ts": retained["ts"], "error": "ssh failed"}
    row = build_row_values("worker.example.com", failed, last_ok=retained)
    assert row["gwhc_worker"] == "stale"
    assert row["gwhc_disk"].startswith("STALE")
    assert build_row_values("worker.example.com", None)["gwhc_worker"] == "unknown"


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"GWHC_ERROR": "gwhc_missing"}, "gwhc_missing"),
        ({"GWHC_ERROR": "unsupported_os", "OS_TYPE": "Darwin"}, "unsupported_os"),
        ({"GWHC_EXIT": "124"}, "gwhc_timeout"),
        ({"GWHC_EXIT": "137"}, "gwhc_timeout"),
        ({"GWHC_EXIT": "2"}, "gwhc_execution_failed"),
        ({"GWHC_STDOUT_B64": "invalid!"}, "invalid_gwhc_encoding"),
        ({"GWHC_STDOUT_B64": base64.b64encode(b"not json").decode()}, "invalid_gwhc_json"),
    ],
)
def test_optional_failures_persist_without_failing_host_gather(tmp_path, changes, reason):
    path = tmp_path / "test.db"
    init_db(path)
    with get_connection(path) as conn:
        info = transport(**changes)
        out = "\n".join(f"{key}={value}" for key, value in info.items())
        result = process_audit_result(
            "worker.example.com", rc=0, out=out, err="", db_conn=conn, actor="test"
        )
        assert result["ok"] is True
        latest, _ = get_latest_host_observations(conn, ["worker.example.com"])
    retained = latest["worker.example.com"]
    assert retained["observed"]["gwhc"]["reason"] == reason
    assert recorded_signals(retained)["worker_status"] == "unknown"


@pytest.mark.parametrize("version", [None, 2, True, "1"])
def test_legacy_and_unsupported_versions_supply_no_metrics(version):
    report = health()
    if version is None:
        del report["schema_version"]
    else:
        report["schema_version"] = version
    signals = recorded_signals(record(report))
    assert signals["worker_status"] == "unknown"
    assert signals["disk_status"] == "unknown"


def test_oversized_output_and_failed_ssh_cannot_supply_metrics():
    oversized = base64.b64encode(b"x" * 262145).decode()
    result = parse_recorded_gwhc(transport(GWHC_STDOUT_B64=oversized), ssh_rc=0)
    assert result["reason"] == "gwhc_output_too_large"
    assert result["stdout"] == ""
    result = parse_recorded_gwhc(transport(), ssh_rc=255)
    assert result["reason"] == "ssh_failed"
    assert result["gwhc_worker"]["status"] == "unknown"
    assert result["gwhc_disks"] == []


def test_missing_component_does_not_discard_other_valid_evidence():
    report = health()
    report["checks"] = report["checks"][:1]
    signals = recorded_signals(record(report))
    assert signals["worker_status"] == "running"
    assert signals["disk_status"] == "unknown"
    report = health()
    report["checks"] = report["checks"][1:]
    signals = recorded_signals(record(report))
    assert signals["worker_status"] == "unknown"
    assert signals["disk_status"] == "ok"


def test_successful_evidence_roundtrips_and_queries(tmp_path):
    path = tmp_path / "test.db"
    init_db(path)
    report = health(free=1)
    with get_connection(path) as conn:
        out = "\n".join(f"{key}={value}" for key, value in transport(report).items())
        process_audit_result(
            "worker.example.com", rc=0, out=out, err="", db_conn=conn, actor="test"
        )
        latest, _ = get_latest_host_observations(conn, ["worker.example.com"])
    rec = latest["worker.example.com"]
    assert rec["observed"]["gwhc"]["gwhc"] == report
    row = build_row_values("worker.example.com", rec)
    text = "gwhc_worker=running gwhc_disk_alert=alert gwhc_age<15m"
    query = parse_query(text)
    assert validate_query(query, text) is None
    assert apply_query([row], query) == [row]


@pytest.mark.parametrize("exit_code", [0, 1, 124])
def test_optional_shell_collection_preserves_exit_and_audit_continues(tmp_path, exit_code):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    payload = json.dumps(health(free=1 if exit_code == 1 else 20))
    commands = {
        "gwhc": f"printf '%s' {shlex.quote(payload)}\nexit {exit_code}",
        "sudo": 'shift\nexec "$@"',
        "timeout": 'shift 3\nexec "$@"',
    }
    for name, body in commands.items():
        script = bindir / name
        script.write_text("#!/bin/sh\n" + body + "\n")
        script.chmod(0o755)
    script = (
        "set -eu\nos_type=Linux\n" + recorded_gwhc_script_body() + "\nprintf 'CORE_CONTINUED=1\\n'"
    )
    completed = subprocess.run(
        ["/bin/sh", "-c", script],
        env={**os.environ, "PATH": f"{bindir}:/usr/bin:/bin"},
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert completed.returncode == 0
    info = parse_kv_lines(completed.stdout)
    assert info["GWHC_EXIT"] == str(exit_code)
    assert info["CORE_CONTINUED"] == "1"
    assert base64.b64decode(info["GWHC_STDOUT_B64"]).decode() == payload
    # Diagnostics must be before the content sentinel so override bytes are unmodified.
    audit = audit_script_body(include_content=True)
    assert audit.index("GWHC_CHECKED_AT") < audit.index("printf '__FLEETROLL_OVERRIDE_CONTENT__")


def test_cli_json_text_and_web_consume_same_recorded_diagnostics(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from fastapi.testclient import TestClient
    from fleetroll.cli import cli
    from fleetroll.commands.monitor.cache import ShaInfoCache
    from fleetroll.commands.monitor.json_output import build_monitor_json_record
    from fleetroll.commands.web.app import create_app
    from fleetroll.db import get_db_path, insert_host_observation

    monkeypatch.setenv("HOME", str(tmp_path))
    path = get_db_path()
    init_db(path)
    rec = {**record(health(free=1)), "host": "worker.example.com", "action": "host.audit"}
    with get_connection(path) as conn:
        insert_host_observation(conn, rec)
        conn.commit()
    runner = CliRunner()
    query = "gwhc_worker=running gwhc_disk_alert=alert"
    result = runner.invoke(
        cli, ["host", "monitor", rec["host"], "--once", "--json", "--filter", query]
    )
    assert result.exit_code == 0, result.output
    output = json.loads(result.output)[rec["host"]]
    assert output["observed"]["gwhc"] == rec["observed"]["gwhc"]
    assert output["gwhc_evidence"]["worker_status"] == "running"
    assert output["gwhc_evidence"]["disk_status"] == "alert"
    assert output["gwhc_evidence"]["observation_ts"] == rec["observed"]["gwhc"]["gwhc_ts"]
    result = runner.invoke(cli, ["host", "monitor", rec["host"], "--once"])
    assert result.exit_code == 0, result.output
    assert all(
        label in result.output
        for label in ("GW_RUN", "DISK", "GWHC_AGE", "ALERT /:1GiB", "running")
    )
    with TestClient(create_app()) as client:
        response = client.get("/api/hosts", params={"filter": query})
    assert response.status_code == 200, response.text
    row = response.json()["rows"][0]
    assert row["gwhc_worker"] == "running"
    assert row["gwhc_disk_alert"] == "alert"
    assert row["gwhc_ts"] == output["gwhc_evidence"]["observation_ts"]
    # Sub-second follow polling must not emit a constantly ticking age.
    cache = ShaInfoCache(tmp_path / "overrides", tmp_path / "vault")
    now = datetime.now(UTC)
    first = build_monitor_json_record(
        rec["host"], record=rec, tc_record=None, sha_cache=cache, now=now
    )
    later = build_monitor_json_record(
        rec["host"], record=rec, tc_record=None, sha_cache=cache, now=now + timedelta(seconds=2)
    )
    from fleetroll.commands.monitor.verification import comparison_record

    assert comparison_record(first) == comparison_record(later)
    stale = build_monitor_json_record(
        rec["host"], record=rec, tc_record=None, sha_cache=cache, now=now + timedelta(minutes=16)
    )
    assert stale["gwhc_evidence"]["worker_status"] == "stale"
    assert stale["gwhc_evidence"]["worker"]["status"] == "running"


@pytest.mark.tui
@skip_no_tmux
def test_tui_filters_recorded_diagnostics(tmp_path, monkeypatch):
    from fleetroll.db import get_db_path, insert_host_observation

    monkeypatch.setenv("HOME", str(tmp_path))
    path = get_db_path()
    init_db(path)
    rec = {**record(health(free=1)), "host": "worker.example.com", "action": "host.audit"}
    with get_connection(path) as conn:
        insert_host_observation(conn, rec)
        conn.commit()
    cmd = f"{shlex.quote(_UV_PATH)} run fleetroll host monitor worker.example.com"
    with TmuxSession(
        cmd=cmd,
        cols=400,
        rows=40,
        env={"HOME": str(tmp_path), "PATH": os.environ["PATH"], "TERM": "xterm-256color"},
    ) as sess:
        assert sess.wait_for("hosts=1", timeout=15), sess.capture()
        sess.send_keys("/", "gwhc_worker=stopped", "Enter")
        assert sess.wait_for("hosts=0/1", timeout=5), sess.capture()


def test_real_timeout_is_bounded_and_does_not_fail_core_collection(tmp_path):
    timeout = shutil.which("timeout") or shutil.which("gtimeout")
    if timeout is None:
        pytest.skip("GNU timeout unavailable")
    for name, body in {
        "gwhc": "exec /bin/sleep 5",
        "sudo": 'shift\nexec "$@"',
        "timeout": f'exec {shlex.quote(timeout)} "$@"',
    }.items():
        script = tmp_path / name
        script.write_text("#!/bin/sh\n" + body + "\n")
        script.chmod(0o755)
    body = (
        "set -eu\nos_type=Linux\n"
        + recorded_gwhc_script_body().replace("20s", "0.1s")
        + "\nprintf 'CORE_CONTINUED=1\\n'"
    )
    result = subprocess.run(
        ["/bin/sh", "-c", body],
        env={**os.environ, "PATH": f"{tmp_path}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    assert result.returncode == 0
    info = parse_kv_lines(result.stdout)
    assert info["CORE_CONTINUED"] == "1"
    assert parse_recorded_gwhc({**info, "OS_TYPE": "Linux"}, ssh_rc=0)["reason"] == "gwhc_timeout"
