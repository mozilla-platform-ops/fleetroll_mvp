"""Tests for the operator-triggered host-reboot-if-idle command."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from fleetroll.cli import cli
from fleetroll.cli_types import HostRebootIfIdleArgs
from fleetroll.commands.reboot import cmd_host_reboot_if_idle, run_reboot_if_idle_for_host
from fleetroll.exceptions import CommandFailureError
from fleetroll.ssh import remote_reboot_if_idle_script


def _args(audit_log: Path, *, confirm: bool = True, json_output: bool = True):
    return HostRebootIfIdleArgs(
        host="worker.example.test",
        ssh_option=None,
        connect_timeout=10,
        timeout=30,
        audit_log=str(audit_log),
        json=json_output,
        confirm=confirm,
    )


def _remote_output(**markers: str) -> str:
    return "\n".join(f"FLEETROLL_REBOOT_IF_IDLE_{key}={value}" for key, value in markers.items())


class TestRunRebootIfIdleForHost:
    def test_requests_reboot_when_live_gwhc_reports_idle(self, mocker, tmp_dir: Path):
        audit_log = tmp_dir / "audit.jsonl"
        run_ssh = mocker.patch("fleetroll.commands.reboot.run_ssh")
        run_ssh.return_value = (
            0,
            _remote_output(
                OS_TYPE="Linux",
                GWHC_PRESENT="1",
                GWHC_EXIT="0",
                JSON_VALID="1",
                GWHC_STATE="IDLE",
                REBOOT_EXIT="0",
                RESULT="requested",
            ),
            "",
        )
        args = _args(audit_log)

        result = run_reboot_if_idle_for_host(
            args.host,
            args=args,
            ssh_opts=[],
            audit_log=audit_log,
            actor="test-operator",
        )

        assert result["ok"] is True
        assert result["outcome"] == "requested"
        assert result["observed"]["gwhc_state"] == "IDLE"
        assert "systemctl --no-block reboot" in run_ssh.call_args.args[1]
        assert "gwhc --json" in run_ssh.call_args.args[1]
        record = json.loads(audit_log.read_text().strip())
        assert record["action"] == "host.reboot_if_idle"
        assert record["outcome"] == "requested"
        assert "raw_stdout" not in record

    @pytest.mark.parametrize(
        ("markers", "reason"),
        [
            (
                {"OS_TYPE": "Darwin", "RESULT": "refused", "REASON": "non_linux_host"},
                "non_linux_host",
            ),
            (
                {
                    "OS_TYPE": "Linux",
                    "GWHC_PRESENT": "0",
                    "RESULT": "refused",
                    "REASON": "gwhc_missing",
                },
                "gwhc_missing",
            ),
            (
                {
                    "OS_TYPE": "Linux",
                    "GWHC_PRESENT": "1",
                    "GWHC_EXIT": "1",
                    "RESULT": "refused",
                    "REASON": "gwhc_failed",
                },
                "gwhc_failed",
            ),
            (
                {
                    "OS_TYPE": "Linux",
                    "GWHC_PRESENT": "1",
                    "GWHC_EXIT": "0",
                    "JSON_VALID": "1",
                    "GWHC_STATE": "BUSY",
                    "RESULT": "refused",
                    "REASON": "state_not_idle",
                },
                "state_not_idle",
            ),
            (
                {
                    "OS_TYPE": "Linux",
                    "GWHC_PRESENT": "1",
                    "GWHC_EXIT": "0",
                    "JSON_VALID": "0",
                    "RESULT": "refused",
                    "REASON": "invalid_or_missing_state",
                },
                "invalid_or_missing_state",
            ),
        ],
    )
    def test_refusal_is_audited_and_never_reported_as_rebooted(
        self, mocker, tmp_dir: Path, markers: dict[str, str], reason: str
    ):
        audit_log = tmp_dir / "audit.jsonl"
        mocker.patch(
            "fleetroll.commands.reboot.run_ssh",
            return_value=(0, _remote_output(**markers), ""),
        )
        args = _args(audit_log)

        result = run_reboot_if_idle_for_host(
            args.host,
            args=args,
            ssh_opts=[],
            audit_log=audit_log,
            actor="test-operator",
        )

        assert result["ok"] is False
        assert result["outcome"] == "refused"
        assert result["reason"] == reason
        assert json.loads(audit_log.read_text().strip())["reason"] == reason

    def test_reboot_request_failure_is_recorded(self, mocker, tmp_dir: Path):
        audit_log = tmp_dir / "audit.jsonl"
        mocker.patch(
            "fleetroll.commands.reboot.run_ssh",
            return_value=(
                0,
                _remote_output(
                    GWHC_EXIT="0",
                    JSON_VALID="1",
                    GWHC_STATE="IDLE",
                    REBOOT_EXIT="1",
                    RESULT="failed",
                    REASON="reboot_request_failed",
                ),
                "systemctl failed",
            ),
        )

        result = run_reboot_if_idle_for_host(
            "worker.example.test",
            args=_args(audit_log),
            ssh_opts=[],
            audit_log=audit_log,
            actor="test-operator",
        )

        assert result["outcome"] == "failed"
        assert result["observed"]["reboot_exit_code"] == 1

    def test_missing_result_after_ssh_disconnect_is_unknown(self, mocker, tmp_dir: Path):
        audit_log = tmp_dir / "audit.jsonl"
        mocker.patch("fleetroll.commands.reboot.run_ssh", return_value=(255, "", "connection lost"))

        result = run_reboot_if_idle_for_host(
            "worker.example.test",
            args=_args(audit_log),
            ssh_opts=[],
            audit_log=audit_log,
            actor="test-operator",
        )

        assert result["outcome"] == "unknown"
        assert result["reason"] == "remote_result_missing"
        assert json.loads(audit_log.read_text().strip())["outcome"] == "unknown"

    def test_dry_run_does_not_connect_or_audit(self, mocker, tmp_dir: Path, capsys):
        audit_log = tmp_dir / "audit.jsonl"
        run_ssh = mocker.patch("fleetroll.commands.reboot.run_ssh")
        args = _args(audit_log, confirm=False, json_output=False)

        cmd_host_reboot_if_idle(args)

        run_ssh.assert_not_called()
        assert "DRY RUN" in capsys.readouterr().out
        assert not audit_log.exists()

    def test_single_host_short_name_expands_in_cli(self, mocker, tmp_dir: Path):
        mocker.patch(
            "fleetroll.commands.reboot.run_ssh",
            return_value=(
                0,
                _remote_output(
                    OS_TYPE="Linux",
                    GWHC_PRESENT="1",
                    GWHC_EXIT="0",
                    JSON_VALID="1",
                    GWHC_STATE="IDLE",
                    REBOOT_EXIT="0",
                    RESULT="requested",
                ),
                "",
            ),
        )
        result = CliRunner().invoke(
            cli,
            [
                "host-reboot-if-idle",
                "t-linux64-ms-004",
                "--confirm",
                "--json",
                "--audit-log",
                str(tmp_dir / "audit.jsonl"),
            ],
        )

        assert result.exit_code == 0
        assert json.loads(result.output)["host"] == "t-linux64-ms-004.test.releng.mdc1.mozilla.com"

    def test_refusal_exits_nonzero_after_json_result(self, mocker, tmp_dir: Path, capsys):
        audit_log = tmp_dir / "audit.jsonl"
        mocker.patch(
            "fleetroll.commands.reboot.run_ssh",
            return_value=(
                0,
                _remote_output(RESULT="refused", REASON="state_not_idle"),
                "",
            ),
        )
        args = _args(audit_log)

        with pytest.raises(CommandFailureError):
            cmd_host_reboot_if_idle(args)

        output = json.loads(capsys.readouterr().out)
        assert output["outcome"] == "refused"
        assert output["reason"] == "state_not_idle"


class TestRemoteRebootIfIdleScript:
    def _run_script(self, tmp_dir: Path, *, gwhc_output: str, reboot_exit: str = "0"):
        bin_dir = tmp_dir / "bin"
        bin_dir.mkdir()
        (bin_dir / "uname").write_text("#!/bin/sh\nprintf 'Linux\\n'\n", encoding="utf-8")
        (bin_dir / "gwhc").write_text(
            "#!/bin/sh\nprintf '%s\\n' " + shlex.quote(gwhc_output) + "\n",
            encoding="utf-8",
        )
        (bin_dir / "sudo").write_text(
            "#!/bin/sh\n"
            '[ "$1" = "-n" ] || exit 90\n'
            "shift\n"
            'if [ "$1" = "gwhc" ]; then shift; exec gwhc "$@"; fi\n'
            'if [ "$1" = "systemctl" ]; then\n'
            "  shift\n"
            '  printf \'%s\\n\' "$*" >> "$REBOOT_REQUESTS"\n'
            '  exit "$SYSTEMCTL_EXIT"\n'
            "fi\n"
            "exit 90\n",
            encoding="utf-8",
        )
        for executable in bin_dir.iterdir():
            executable.chmod(0o755)
        reboot_requests = tmp_dir / "reboot-requests.txt"
        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env["REBOOT_REQUESTS"] = str(reboot_requests)
        env["SYSTEMCTL_EXIT"] = reboot_exit
        command = shlex.split(remote_reboot_if_idle_script())
        completed = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
        return completed, reboot_requests

    def test_idle_report_requests_one_reboot(self, tmp_dir: Path):
        completed, requests = self._run_script(tmp_dir, gwhc_output='{"state":"IDLE"}')

        assert completed.returncode == 0
        assert "FLEETROLL_REBOOT_IF_IDLE_RESULT=requested" in completed.stdout
        assert requests.read_text().splitlines() == ["--no-block reboot"]

    def test_non_idle_report_never_requests_reboot(self, tmp_dir: Path):
        completed, requests = self._run_script(tmp_dir, gwhc_output='{"state":"UNKNOWN"}')

        assert completed.returncode == 0
        assert "FLEETROLL_REBOOT_IF_IDLE_RESULT=refused" in completed.stdout
        assert "FLEETROLL_REBOOT_IF_IDLE_REASON=state_not_idle" in completed.stdout
        assert not requests.exists()

    def test_malformed_json_never_requests_reboot(self, tmp_dir: Path):
        completed, requests = self._run_script(tmp_dir, gwhc_output="not-json")

        assert completed.returncode == 0
        assert "FLEETROLL_REBOOT_IF_IDLE_REASON=invalid_or_missing_state" in completed.stdout
        assert not requests.exists()
