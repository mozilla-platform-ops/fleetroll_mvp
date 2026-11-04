"""Live idle checks: evidence handling, CLI output, and remote collection."""

import json
import os
import shlex
import subprocess

import pytest
from click.testing import CliRunner
from fleetroll.cli import cli
from fleetroll.commands.idle import parse_idle_output
from fleetroll.ssh import remote_idle_script


def _output(*, state="IDLE", puppet="false", payload=None, **markers):
    values = {
        "CHECKED_AT": "2026-11-04T22:39:12Z",
        "OS_TYPE": "Linux",
        "PUPPET_RUNNING": puppet,
        "GWHC_EXIT": "0",
        **markers,
    }
    envelope = "\n".join(f"FLEETROLL_IDLE_{key}={value}" for key, value in values.items())
    if payload is None:
        payload = json.dumps(
            {
                "ok": True,
                "state": state,
                "state_description": "Live worker evidence",
                "ts": "2026-11-04T22:39:13Z",
                "checks": [
                    {"name": "generic_worker_process", "status": "pass", "detail": "task running"}
                ],
            }
        )
    return f"{envelope}\nFLEETROLL_IDLE_JSON_BEGIN=\n{payload}\n"


@pytest.mark.parametrize(
    ("state", "puppet", "worker", "idle"),
    [
        ("IDLE", "false", "idle", True),
        ("IDLE", "true", "idle", False),
        ("WORKING", "false", "working", False),
        ("BUSY", "true", "working", False),
    ],
)
def test_known_states(state, puppet, worker, idle):
    result = parse_idle_output(_output(state=state, puppet=puppet), ssh_rc=0)
    assert result["ok"] is True
    assert result["worker_state"] == worker
    assert result["idle"] is idle
    assert result["puppet_running"] is (puppet == "true")
    assert result["checked_at"] == "2026-11-04T22:39:12Z"
    assert result["gwhc_ts"] == "2026-11-04T22:39:13Z"
    assert result["evidence"][0]["detail"] == "task running"


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"state": "UNKNOWN"}, "unknown_worker_state"),
        ({"state": "NEW_STATE"}, "unknown_worker_state"),
        ({"state": None}, "unknown_worker_state"),
        ({"state": []}, "unknown_worker_state"),
        ({"puppet": "unknown"}, "puppet_activity_unknown"),
        ({"GWHC_EXIT": "1"}, "gwhc_failed"),
        ({"ERROR": "gwhc_missing"}, "gwhc_missing"),
        ({"OS_TYPE": "Darwin", "ERROR": "unsupported_os"}, "unsupported_os"),
        ({"payload": "not JSON"}, "invalid_gwhc_json"),
        ({"payload": "[]"}, "invalid_gwhc_json"),
        ({"payload": '{"ok":false,"state":"IDLE"}'}, "gwhc_unhealthy"),
        ({"payload": '{"state":"IDLE"}'}, "gwhc_unhealthy"),
        ({"payload": '{"ok":"yes","state":"IDLE"}'}, "gwhc_unhealthy"),
        ({"CHECKED_AT": ""}, "check_timestamp_missing"),
    ],
)
def test_incomplete_evidence_never_claims_idle(kwargs, reason):
    result = parse_idle_output(_output(**kwargs), ssh_rc=0)
    assert result["ok"] is False
    assert result["idle"] is None
    assert result["reason"] == reason


def test_ssh_failure_invalidates_partial_idle_evidence():
    result = parse_idle_output(_output(), ssh_rc=124)
    assert result["reason"] == "ssh_failed"
    assert result["idle"] is None
    assert result["puppet_running"] is None


@pytest.mark.parametrize("state", ["IDLE", "WORKING", "UNKNOWN"])
def test_cli_json_and_exit_status(mocker, state):
    ssh = mocker.patch(
        "fleetroll.commands.idle.run_ssh", return_value=(0, _output(state=state), "")
    )
    result = CliRunner().invoke(
        cli,
        [
            "host",
            "idle",
            "t-linux64-ms-017",
            "--json",
            "--timeout",
            "12",
            "--ssh-option",
            "-p 2222",
        ],
    )
    assert result.exit_code == (1 if state == "UNKNOWN" else 0)
    data = json.loads(result.output)
    assert data["host"] == "t-linux64-ms-017.test.releng.mdc1.mozilla.com"
    assert data["gwhc_state"] == state
    assert data["ts"]
    assert ssh.call_args.kwargs["timeout_s"] == 12
    assert ssh.call_args.kwargs["ssh_options"][-2:] == ["-p", "2222"]


def test_text_reports_worker_and_puppet_separately(mocker):
    mocker.patch("fleetroll.commands.idle.run_ssh", return_value=(0, _output(puppet="true"), ""))
    result = CliRunner().invoke(cli, ["host", "idle", "worker.example.test"])
    assert result.exit_code == 0
    assert "Worker: idle; Puppet: running; Host idle: no" in result.output
    assert "2026-11-04T22:39:12Z" in result.output
    assert "Snapshot only" in result.output


def test_ssh_exception_still_emits_json(mocker):
    mocker.patch("fleetroll.commands.idle.run_ssh", side_effect=OSError("connection failed"))
    result = CliRunner().invoke(cli, ["host", "idle", "worker.example.test", "--json"])
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["reason"] == "ssh_error"
    assert data["idle"] is None


def test_host_file_is_rejected_without_connecting(mocker, tmp_path):
    hosts = tmp_path / "hosts.list"
    hosts.write_text("worker.example.test\n")
    ssh = mocker.patch("fleetroll.commands.idle.run_ssh")
    result = CliRunner().invoke(cli, ["host", "idle", str(hosts)])
    assert result.exit_code != 0
    ssh.assert_not_called()


@pytest.mark.parametrize(
    ("os_type", "pgrep_exit", "gwhc_exit", "sudo_denied", "expected_reason", "expected_puppet"),
    [
        ("Linux", 1, 0, False, "worker_idle", False),
        ("Linux", 0, 0, False, "puppet_running", True),
        ("Linux", 2, 0, False, "puppet_activity_unknown", None),
        ("Linux", 1, 0, True, "puppet_activity_unknown", None),
        ("Linux", 1, 1, False, "gwhc_failed", False),
        ("Darwin", 1, 0, False, "unsupported_os", None),
    ],
)
def test_remote_collection(
    tmp_path, os_type, pgrep_exit, gwhc_exit, sudo_denied, expected_reason, expected_puppet
):
    """Execute the generated shell script with fake read-only host commands."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    scripts = {
        "uname": f"printf '%s\\n' {os_type}",
        "pgrep": f"printf 'process args must not leak\\n'; exit {pgrep_exit}",
        "gwhc": "printf '%s\\n' "
        + shlex.quote('{"ok":true,"state":"IDLE"}')
        + f"; exit {gwhc_exit}",
        "sudo": (
            '[ "$1" = "-n" ] || exit 90\nshift\n'
            + ('[ "$1" = "sh" ] && exit 1\n' if sudo_denied else "")
            + 'exec "$@"'
        ),
    }
    for name, script in scripts.items():
        executable = bin_dir / name
        executable.write_text(f"#!/bin/sh\n{script}\n")
        executable.chmod(0o755)
    completed = subprocess.run(
        shlex.split(remote_idle_script()),
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "process args" not in completed.stdout
    result = parse_idle_output(completed.stdout, ssh_rc=completed.returncode)
    assert result["reason"] == expected_reason
    assert result["puppet_running"] is expected_puppet
