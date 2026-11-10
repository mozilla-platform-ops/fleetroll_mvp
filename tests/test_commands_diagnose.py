"""Live diagnosis retains unhealthy evidence without authorizing recovery."""

import json
import os
import shlex
import subprocess

import pytest
from click.testing import CliRunner
from fleetroll.cli import cli
from fleetroll.commands.diagnose import parse_diagnose_output
from fleetroll.ssh import remote_diagnose_script


def _health(*, unhealthy=False):
    return {
        "ok": not unhealthy,
        "state": "DEGRADED" if unhealthy else "IDLE",
        "host": "worker.example.com",
        "ts": "2026-11-05T08:00:00Z",
        "checks": [
            {
                "name": "generic_worker_process",
                "status": "fail" if unhealthy else "pass",
                "detail": "process evidence",
                "data": {"zombie_pids": [123] if unhealthy else [], "custom": {"nested": True}},
            }
        ],
    }


def _output(health=None, *, exit_code=0, payload=None, **markers):
    values = {
        "CHECKED_AT": "2026-11-05T07:59:59Z",
        "OS_TYPE": "Linux",
        "GWHC_EXIT": str(exit_code),
        **markers,
    }
    envelope = "\n".join(f"FLEETROLL_DIAGNOSE_{key}={value}" for key, value in values.items())
    payload = (
        json.dumps(health if health is not None else _health()) if payload is None else payload
    )
    return f"{envelope}\nFLEETROLL_DIAGNOSE_JSON_BEGIN=\n{payload}\n"


@pytest.mark.parametrize("unhealthy", [False, True])
def test_completed_diagnosis_preserves_structured_checks(unhealthy):
    health = _health(unhealthy=unhealthy)
    result = parse_diagnose_output(_output(health, exit_code=int(unhealthy)), ssh_rc=0)
    assert result["ok"] is True
    assert result["collection_status"] == "completed"
    assert result["health"] == ("unhealthy" if unhealthy else "healthy")
    assert result["checks"] == health["checks"]
    assert result["gwhc"] == health
    assert result["gwhc_exit_code"] == int(unhealthy)
    assert result["contract_status"] == "legacy"
    assert result["zombie"] == {"status": "unknown", "reason": "unversioned_gwhc_schema"}


@pytest.mark.parametrize("version", [None, 1])
def test_unrelated_degraded_never_implies_zombie(version):
    health = _health(unhealthy=True)
    if version is not None:
        health["schema_version"] = version
    health["checks"][0].update(name="disk_free", data={"free_gb": 0})
    result = parse_diagnose_output(_output(health, exit_code=1), ssh_rc=0)
    assert result["health"] == "unhealthy"
    assert result["zombie"]["status"] == "unknown"


@pytest.mark.parametrize("version", [0, 999, None, "1", True, False, 1.0, {}, []])
def test_unsupported_contracts_preserve_raw_evidence(version):
    health = {**_health(unhealthy=True), "schema_version": version}
    result = parse_diagnose_output(_output(health, exit_code=1), ssh_rc=0)
    assert result["ok"] is False
    assert result["reason"] == "unsupported_gwhc_schema"
    assert result["gwhc_schema_version"] == version
    assert result["gwhc"] == health
    assert result["health"] == "unknown"
    assert result["zombie"]["status"] == "unknown"


def _v1_health(*, unhealthy=False):
    health = {**_health(unhealthy=unhealthy), "schema_version": 1}
    health["checks"][0]["data"]["processes"] = [
        {"pid": 123, "ppid": 1, "state": "Z" if unhealthy else "S"}
    ]
    return health


@pytest.mark.parametrize("unhealthy", [False, True])
def test_supported_v1_health_and_zombies(unhealthy):
    health = _v1_health(unhealthy=unhealthy)
    health["future_field"] = {"preserve": True}
    result = parse_diagnose_output(_output(health, exit_code=int(unhealthy)), ssh_rc=0)
    assert result["schema_version"] == 2
    assert result["gwhc_schema_version"] == 1
    assert result["contract_status"] == "supported"
    assert result["ok"] is True
    assert result["health"] == ("unhealthy" if unhealthy else "healthy")
    assert result["zombie"]["status"] == ("present" if unhealthy else "absent")
    assert result["gwhc"] == health
    assert result["checks"] == health["checks"]
    assert result["gwhc_ts"] == health["ts"]
    assert result["reported_host"] == health["host"]


def test_v1_unrelated_failure_preserves_verified_zombie_absence():
    health = _v1_health()
    health.update(ok=False, state="DEGRADED")
    health["checks"].append(
        {"name": "disk_free", "status": "fail", "detail": "full", "data": {"free_gb": 0}}
    )
    result = parse_diagnose_output(_output(health, exit_code=1), ssh_rc=0)
    assert result["ok"] is True
    assert result["health"] == "unhealthy"
    assert result["zombie"]["status"] == "absent"


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"zombie_pids": []},
        {"zombie_pids": [], "processes": []},
        {"zombie_pids": None, "processes": []},
        {"zombie_pids": [True], "processes": []},
        {"zombie_pids": [0], "processes": []},
        {"zombie_pids": ["123"], "processes": []},
        {"zombie_pids": [123, 123], "processes": []},
        {"zombie_pids": [], "processes": {}},
        {"zombie_pids": [], "processes": [None]},
        {"zombie_pids": [], "processes": [{"pid": 123, "state": "S"}]},
        {"zombie_pids": [], "processes": [{"pid": True, "ppid": 1, "state": "S"}]},
        {"zombie_pids": [], "processes": [{"pid": 123, "ppid": -1, "state": "S"}]},
        {"zombie_pids": [], "processes": [{"pid": 123, "ppid": 1, "state": "future"}]},
        {"zombie_pids": [], "processes": [{"pid": 123, "ppid": 1, "state": "Z"}]},
        {"zombie_pids": [123], "processes": [{"pid": 123, "ppid": 1, "state": "S"}]},
    ],
)
def test_v1_incomplete_or_malformed_zombie_evidence_remains_unknown(data):
    health = _v1_health()
    health["checks"][0]["data"] = data
    result = parse_diagnose_output(_output(health), ssh_rc=0)
    assert result["ok"] is True  # General diagnosis survives unavailable zombie evidence.
    assert result["zombie"]["status"] == "unknown"
    assert result["gwhc"] == health


@pytest.mark.parametrize("status", ["pass", "warn", "fail"])
@pytest.mark.parametrize("unverified", [[], [456]])
def test_v1_absence_requires_successful_process_inspection(status, unverified):
    health = _v1_health()
    health["checks"][0].update(status=status)
    health["checks"][0]["data"]["unverified_pids"] = unverified
    health["ok"] = status != "fail"
    result = parse_diagnose_output(_output(health, exit_code=int(not health["ok"])), ssh_rc=0)
    assert result["ok"] is True
    expected = "unknown" if status == "fail" or unverified else "absent"
    assert result["zombie"]["status"] == expected


@pytest.mark.parametrize("status", ["pass", "warn", "fail"])
def test_v1_confirmed_zombie_requires_failing_check(status):
    health = _v1_health(unhealthy=True)
    health["checks"][0].update(status=status)
    health["checks"][0]["data"]["unverified_pids"] = [456]
    health["ok"] = status != "fail"
    result = parse_diagnose_output(_output(health, exit_code=int(not health["ok"])), ssh_rc=0)
    assert result["zombie"]["status"] == ("present" if status == "fail" else "unknown")


@pytest.mark.parametrize(
    "kind", ["duplicate_check", "duplicate_pid", "overlap", "bad_unverified", "dead"]
)
def test_v1_ambiguous_process_inspection(kind):
    health = _v1_health()
    check = health["checks"][0]
    if kind == "duplicate_check":
        health["checks"].append(check.copy())
    elif kind == "duplicate_pid":
        check["data"]["processes"] *= 2
    elif kind == "overlap":
        check["data"]["unverified_pids"] = [123]
    elif kind == "bad_unverified":
        check["data"]["unverified_pids"] = None
    else:
        check["data"]["processes"][0]["state"] = "X"
    result = parse_diagnose_output(_output(health), ssh_rc=0)
    assert result["zombie"]["status"] == "unknown"


def test_v1_held_is_independent_of_health_and_zombies():
    health = _v1_health(unhealthy=True)
    health["state"] = "HELD"
    health["checks"].append(
        {"name": "operator_hold", "status": "warn", "detail": "held", "data": {"active": True}}
    )
    result = parse_diagnose_output(_output(health, exit_code=1), ssh_rc=0)
    assert result["ok"] is True
    assert result["health"] == "unhealthy"
    assert result["gwhc_state"] == "HELD"
    assert result["zombie"]["status"] == "present"
    assert result["checks"][-1]["data"]["active"] is True


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"payload": "bad JSON"}, "invalid_gwhc_json"),
        ({"payload": "[]"}, "invalid_gwhc_json"),
        ({"payload": "{}"}, "invalid_gwhc_report"),
        ({"exit_code": 2}, "gwhc_execution_failed"),
        ({"exit_code": 127, "payload": ""}, "gwhc_execution_failed"),
        ({"exit_code": 1, "payload": ""}, "invalid_gwhc_json"),
        ({"exit_code": 1}, "inconsistent_gwhc_result"),
        ({"GWHC_EXIT": ""}, "gwhc_exit_missing"),
        ({"ERROR": "gwhc_missing"}, "gwhc_missing"),
        ({"OS_TYPE": "Darwin"}, "unsupported_os"),
        ({"CHECKED_AT": ""}, "check_timestamp_invalid"),
    ],
)
def test_collection_failures(kwargs, reason):
    result = parse_diagnose_output(_output(**kwargs), ssh_rc=0)
    assert result["reason"] == reason
    assert result["ok"] is False
    assert result["collection_status"] == "failed"
    assert result["health"] == "unknown"


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("ok", "true", "invalid_gwhc_report"),
        ("ts", "2026-11-05T08:00:00", "invalid_gwhc_report"),
        ("checks", [], "invalid_gwhc_report"),
        ("checks", [None], "invalid_gwhc_checks"),
        ("checks", [{"name": "process", "status": "new"}], "invalid_gwhc_checks"),
        ("ok", False, "inconsistent_gwhc_result"),
    ],
)
@pytest.mark.parametrize("version", [None, 1])
def test_malformed_and_inconsistent_reports(field, value, reason, version):
    health = {**_health(), field: value}
    if version is not None:
        health["schema_version"] = version
    result = parse_diagnose_output(_output(health), ssh_rc=0)
    assert result["reason"] == reason
    assert result["gwhc"] == health
    assert result["zombie"]["status"] == "unknown"


@pytest.mark.parametrize(("rc", "reason"), [(255, "ssh_failed"), (124, "ssh_timeout")])
def test_transport_failure_invalidates_partial_output(rc, reason):
    result = parse_diagnose_output(_output(), ssh_rc=rc)
    assert result["reason"] == reason
    assert result["gwhc"] is None
    assert result["checks"] == []


@pytest.mark.parametrize("unhealthy", [False, True])
@pytest.mark.parametrize("versioned", [False, True])
def test_cli_completed_health_exits_zero(mocker, unhealthy, versioned):
    health = _v1_health(unhealthy=unhealthy) if versioned else _health(unhealthy=unhealthy)
    ssh = mocker.patch(
        "fleetroll.commands.diagnose.run_ssh",
        return_value=(0, _output(health, exit_code=int(unhealthy)), ""),
    )
    result = CliRunner().invoke(
        cli, ["host", "diagnose", "t-linux64-ms-017", "--json", "--timeout", "12"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["schema_version"] == 2
    assert data["host"] == "t-linux64-ms-017.test.releng.mdc1.mozilla.com"
    assert data["reported_host"] == "worker.example.com"
    assert data["ts"]
    assert ssh.call_args.kwargs["timeout_s"] == 12


def test_cli_text_displays_normalized_zombie(mocker):
    mocker.patch(
        "fleetroll.commands.diagnose.run_ssh",
        return_value=(0, _output(_v1_health(unhealthy=True), exit_code=1), ""),
    )
    result = CliRunner().invoke(cli, ["host", "diagnose", "worker.example.com"])
    assert result.exit_code == 0
    assert "Zombie: present (confirmed_zombie_processes)" in result.output


def test_cli_failures_emit_json(mocker):
    mocker.patch("fleetroll.commands.diagnose.run_ssh", return_value=(124, "", "timeout"))
    result = CliRunner().invoke(cli, ["host", "diagnose", "worker.example.com", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["reason"] == "ssh_timeout"


def test_cli_exception_and_text(mocker):
    mocker.patch("fleetroll.commands.diagnose.run_ssh", side_effect=OSError("connection failed"))
    result = CliRunner().invoke(cli, ["host", "diagnose", "worker.example.com"])
    assert result.exit_code == 1
    assert "Collection: failed; health: unknown" in result.output
    assert "ssh_error" in result.output


def test_cli_rejects_file(mocker, tmp_path):
    hosts = tmp_path / "hosts.list"
    hosts.write_text("worker.example.com\n")
    ssh = mocker.patch("fleetroll.commands.diagnose.run_ssh")
    result = CliRunner().invoke(cli, ["host", "diagnose", str(hosts)])
    assert result.exit_code != 0
    ssh.assert_not_called()


@pytest.mark.parametrize("unhealthy", [False, True])
@pytest.mark.parametrize("versioned", [False, True])
def test_remote_script_preserves_nonzero_check_output(tmp_path, unhealthy, versioned):
    health = _v1_health(unhealthy=unhealthy) if versioned else _health(unhealthy=unhealthy)
    scripts = {
        "uname": "printf 'Linux\\n'",
        "gwhc": f"printf '%s\\n' {shlex.quote(json.dumps(health))}\nexit {int(unhealthy)}",
        "sudo": '[ "$1" = "-n" ] || exit 90\nshift\nexec "$@"',
    }
    for name, script in scripts.items():
        path = tmp_path / name
        path.write_text(f"#!/bin/sh\n{script}\n")
        path.chmod(0o755)
    completed = subprocess.run(
        shlex.split(remote_diagnose_script()),
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    result = parse_diagnose_output(completed.stdout, ssh_rc=completed.returncode)
    assert result["ok"] is True
    assert result["gwhc_exit_code"] == int(unhealthy)
    assert result["checks"] == health["checks"]
