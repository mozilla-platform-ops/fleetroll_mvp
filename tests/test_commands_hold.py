"""Operator hold confirmation, live verification, and audited mutations."""

import json
import os
import shlex
import subprocess

import pytest
from click.testing import CliRunner
from fleetroll.cli import cli
from fleetroll.commands.hold import parse_hold_output
from fleetroll.ssh import remote_hold_script


def output(*, result="changed", before="0", present="1", **extra):
    markers = {
        "OS_TYPE": "Linux",
        "CHECKED_AT": "2026-11-05T00:00:00Z",
        "RESULT": result,
        "BEFORE": before,
        "PRESENT": present,
        **extra,
    }
    return "\n".join(f"FLEETROLL_HOLD_{key}={value}" for key, value in markers.items())


@pytest.fixture
def remote(tmp_path):
    """Run generated scripts locally with sudo and OS stubs; never access /home."""
    home = tmp_path / "cltbld"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "sudo-calls"
    scripts = {
        "uname": 'printf "%s\\n" "$TEST_OS"',
        "sudo": '''printf "sudo\\n" >> "$TEST_CALLS"
[ "$1" = -n ] || exit 90
shift
[ "$TEST_DENIED" != 1 ] || exit 1
exec "$@"''',
    }
    for name, script in scripts.items():
        path = bin_dir / name
        path.write_text(f"#!/bin/sh\n{script}\n")
        path.chmod(0o755)

    def run(operation, *, os_type="Linux", denied=False):
        command = remote_hold_script(operation).replace("/home/cltbld", str(home))
        result = subprocess.run(
            shlex.split(command),
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "TEST_OS": os_type,
                "TEST_DENIED": "1" if denied else "0",
                "TEST_CALLS": str(calls),
            },
        )
        return parse_hold_output(result.stdout, ssh_rc=result.returncode, operation=operation)

    return run, home / "operator_hold", calls, bin_dir


def test_remote_lifecycle_is_verified_and_idempotent(remote):
    run, flag, _, _ = remote
    assert run("show")["operator_hold"] is False
    assert run("set")["outcome"] == "changed"
    assert flag.is_file()
    flag.write_text("existing metadata\n")
    modified = flag.stat().st_mtime_ns
    assert run("set")["outcome"] == "no_change"
    assert flag.read_text() == "existing metadata\n"
    assert flag.stat().st_mtime_ns == modified
    assert run("show")["operator_hold"] is True
    cleared = run("clear")
    assert cleared["ok"]
    assert cleared["operator_hold"] is False
    assert not flag.exists()
    assert run("clear")["outcome"] == "no_change"


def test_non_linux_refused_before_sudo(remote):
    run, flag, calls, _ = remote
    result = run("set", os_type="Darwin")
    assert result["reason"] == "unsupported_os"
    assert result["operator_hold"] is None
    assert not flag.exists()
    assert not calls.exists()


@pytest.mark.parametrize("operation", ["show", "set", "clear"])
def test_sudo_failure_never_claims_hold_state(remote, operation):
    run, flag, _, _ = remote
    result = run(operation, denied=True)
    assert not result["ok"]
    assert result["operator_hold"] is None
    assert not flag.exists()


@pytest.mark.parametrize("kind", ["symlink", "dangling", "directory"])
@pytest.mark.parametrize("operation", ["show", "set", "clear"])
def test_nonregular_flag_is_refused(remote, kind, operation):
    run, flag, _, _ = remote
    target = flag.parent / "target"
    if kind == "directory":
        flag.mkdir()
    else:
        if kind == "symlink":
            target.write_text("preserve target")
        flag.symlink_to(target)
    result = run(operation)
    assert not result["ok"]
    assert result["reason"] == "unsupported_flag_type"
    assert flag.exists() or flag.is_symlink()
    if target.exists():
        assert target.read_text() == "preserve target"


def test_clear_failure_is_not_success(remote):
    run, flag, _, bin_dir = remote
    flag.touch()
    rm = bin_dir / "rm"
    rm.write_text("#!/bin/sh\nexit 1\n")
    rm.chmod(0o755)
    result = run("clear")
    assert result["reason"] == "remove_failed"
    assert result["operator_hold"] is None
    assert flag.exists()


def test_successful_remove_command_still_requires_absence(remote):
    run, flag, _, bin_dir = remote
    flag.touch()
    rm = bin_dir / "rm"
    rm.write_text("#!/bin/sh\nexit 0\n")
    rm.chmod(0o755)
    result = run("clear")
    assert not result["ok"]
    assert result["reason"] == "verification_failed"
    assert result["operator_hold"] is None
    assert flag.exists()


def test_missing_worker_home_is_refused(remote):
    run, flag, _, _ = remote
    flag.parent.rmdir()
    result = run("set")
    assert result["reason"] == "worker_home_missing"
    assert not result["ok"]
    assert not flag.exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"CHECKED_AT": ""},
        {"OS_TYPE": "Darwin"},
        {"present": "unknown"},
        {"present": "0"},
        {"result": "no_change"},
        {"before": "unknown"},
        {"result": "observed"},
    ],
)
def test_inconsistent_evidence_never_reports_a_successful_change(changes):
    result = parse_hold_output(output(**changes), ssh_rc=0, operation="set")
    assert not result["ok"]
    assert result["operator_hold"] is None


def test_disconnect_invalidates_success_markers():
    result = parse_hold_output(output(), ssh_rc=255, operation="set")
    assert not result["ok"]
    assert result["operator_hold"] is None


@pytest.mark.parametrize("operation", ["set", "clear"])
def test_mutations_require_confirmation_without_ssh_or_audit(mocker, tmp_path, operation):
    ssh = mocker.patch("fleetroll.commands.hold.run_ssh")
    audit = tmp_path / "audit.jsonl"
    result = CliRunner().invoke(
        cli, ["host", "hold", operation, "worker.example.test", "--json", "--audit-log", str(audit)]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["dry_run"]
    ssh.assert_not_called()
    assert not audit.exists()


@pytest.mark.parametrize(
    ("operation", "before", "present", "outcome"),
    [
        ("set", "0", "1", "changed"),
        ("set", "1", "1", "no_change"),
        ("clear", "1", "0", "changed"),
        ("clear", "0", "0", "no_change"),
    ],
)
def test_confirmed_changes_and_noops_are_audited(
    mocker, tmp_path, operation, before, present, outcome
):
    ssh = mocker.patch(
        "fleetroll.commands.hold.run_ssh",
        return_value=(0, output(before=before, present=present, result=outcome), ""),
    )
    mocker.patch("fleetroll.commands.hold.infer_actor", return_value="test-operator")
    audit = tmp_path / "audit.jsonl"
    result = CliRunner().invoke(
        cli,
        [
            "host",
            "hold",
            operation,
            "user@t-linux64-ms-017",
            "--confirm",
            "--reason",
            "maintenance",
            "--json",
            "--audit-log",
            str(audit),
            "--timeout",
            "12",
            "--ssh-option",
            "-p 2222",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data == json.loads(audit.read_text())
    assert data["actor"] == "test-operator"
    assert data["parameters"]["reason"] == "maintenance"
    assert data["action"] == f"host.hold.{operation}"
    assert data["outcome"] == outcome
    assert ssh.call_args.args[0] == "user@t-linux64-ms-017.test.releng.mdc1.mozilla.com"
    assert ssh.call_args.kwargs["timeout_s"] == 12
    assert ssh.call_args.kwargs["ssh_options"][-2:] == ["-p", "2222"]


@pytest.mark.parametrize("exception", [False, True])
def test_failed_write_emits_json_and_audit_record(mocker, tmp_path, exception):
    ssh = mocker.patch("fleetroll.commands.hold.run_ssh")
    if exception:
        ssh.side_effect = OSError("connection failed")
    else:
        ssh.return_value = (255, output(), "connection lost")
    audit = tmp_path / "audit.jsonl"
    result = CliRunner().invoke(
        cli,
        [
            "host",
            "hold",
            "set",
            "worker.example.test",
            "--confirm",
            "--json",
            "--audit-log",
            str(audit),
        ],
    )
    assert result.exit_code == 1, result.output
    data = json.loads(result.output)
    assert data["operator_hold"] is None
    assert not data["ok"]
    assert json.loads(audit.read_text()) == data


def test_show_is_live_without_confirmation_or_audit(mocker, tmp_path):
    ssh = mocker.patch(
        "fleetroll.commands.hold.run_ssh",
        return_value=(0, output(result="observed", before="1"), ""),
    )
    audit = tmp_path / "audit.jsonl"
    result = CliRunner().invoke(
        cli, ["host", "hold", "show", "worker.example.test", "--audit-log", str(audit)]
    )
    assert result.exit_code == 0, result.output
    assert "operator hold" in result.output.lower()
    assert "already-running job" in result.output
    ssh.assert_called_once()
    assert not audit.exists()


def test_host_files_and_multiple_hosts_are_rejected(mocker, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "hosts.list").write_text("worker.example.test\n")
    ssh = mocker.patch("fleetroll.commands.hold.run_ssh")
    for hosts in (["hosts.list"], ["first.example.test", "second.example.test"]):
        result = CliRunner().invoke(cli, ["host", "hold", "set", *hosts, "--confirm"])
        assert result.exit_code != 0
    ssh.assert_not_called()
