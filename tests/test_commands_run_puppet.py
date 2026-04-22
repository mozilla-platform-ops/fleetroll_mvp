"""Tests for fleetroll/commands/run_puppet.py - host-run-puppet command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fleetroll.cli_types import HostRunPuppetArgs
from fleetroll.commands.run_puppet import cmd_host_run_puppet
from fleetroll.exceptions import CommandFailureError


class TestCmdHostRunPuppet:
    """Tests for cmd_host_run_puppet function."""

    def test_dry_run_without_confirm(self, mocker, mock_args_run_puppet: HostRunPuppetArgs):
        """Prints summary and exits when --confirm not provided."""
        mock_args_run_puppet.confirm = False
        mock_args_run_puppet.json = False
        mock_run_ssh = mocker.patch("fleetroll.commands.run_puppet.run_ssh")
        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0]))

        cmd_host_run_puppet(mock_args_run_puppet)

        mock_run_ssh.assert_not_called()
        assert any("DRY RUN" in str(line) for line in captured)

    def test_single_host_success_no_changes(
        self, mocker, mock_args_run_puppet: HostRunPuppetArgs, tmp_dir: Path
    ):
        """Successfully runs puppet with no changes (EXIT=0)."""
        mock_args_run_puppet.audit_log = str(tmp_dir / "audit.jsonl")
        mock_args_run_puppet.json = True

        mock_run_ssh = mocker.patch("fleetroll.commands.run_puppet.run_ssh")
        mock_run_ssh.return_value = (0, "some puppet output\nEXIT=0\n", "")

        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0]))

        cmd_host_run_puppet(mock_args_run_puppet)

        output = json.loads(captured[0])
        assert output["ok"] is True
        assert output["action"] == "host.run_puppet"
        assert output["observed"]["puppet_exit"] == 0
        assert output["observed"]["changes_applied"] is False

    def test_single_host_success_with_changes(
        self, mocker, mock_args_run_puppet: HostRunPuppetArgs, tmp_dir: Path
    ):
        """Successfully runs puppet with changes applied (EXIT=2)."""
        mock_args_run_puppet.audit_log = str(tmp_dir / "audit.jsonl")
        mock_args_run_puppet.json = True

        mock_run_ssh = mocker.patch("fleetroll.commands.run_puppet.run_ssh")
        mock_run_ssh.return_value = (0, "applied changes\nEXIT=2\n", "")

        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0]))

        cmd_host_run_puppet(mock_args_run_puppet)

        output = json.loads(captured[0])
        assert output["ok"] is True
        assert output["observed"]["puppet_exit"] == 2
        assert output["observed"]["changes_applied"] is True

    def test_single_host_puppet_failure(
        self, mocker, mock_args_run_puppet: HostRunPuppetArgs, tmp_dir: Path
    ):
        """Raises CommandFailureError when puppet exits with failure code."""
        mock_args_run_puppet.audit_log = str(tmp_dir / "audit.jsonl")
        mock_args_run_puppet.json = True

        mock_run_ssh = mocker.patch("fleetroll.commands.run_puppet.run_ssh")
        mock_run_ssh.return_value = (1, "puppet failed\nEXIT=1\n", "catalog failed")

        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0]))

        with pytest.raises(CommandFailureError):
            cmd_host_run_puppet(mock_args_run_puppet)

        output = json.loads(captured[0])
        assert output["ok"] is False
        assert output["observed"]["puppet_exit"] == 1

    def test_single_host_puppet_failure_with_changes(
        self, mocker, mock_args_run_puppet: HostRunPuppetArgs, tmp_dir: Path
    ):
        """Raises CommandFailureError when puppet exits 4 (failure + changes)."""
        mock_args_run_puppet.audit_log = str(tmp_dir / "audit.jsonl")
        mock_args_run_puppet.json = True

        mock_run_ssh = mocker.patch("fleetroll.commands.run_puppet.run_ssh")
        mock_run_ssh.return_value = (1, "partial failure\nEXIT=4\n", "resource failed")

        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0]))

        with pytest.raises(CommandFailureError):
            cmd_host_run_puppet(mock_args_run_puppet)

        output = json.loads(captured[0])
        assert output["ok"] is False
        assert output["observed"]["puppet_exit"] == 4

    def test_records_reason(self, mocker, mock_args_run_puppet: HostRunPuppetArgs, tmp_dir: Path):
        """Result includes reason when provided."""
        mock_args_run_puppet.audit_log = str(tmp_dir / "audit.jsonl")
        mock_args_run_puppet.json = True
        mock_args_run_puppet.reason = "post-override puppet run"

        mock_run_ssh = mocker.patch("fleetroll.commands.run_puppet.run_ssh")
        mock_run_ssh.return_value = (0, "EXIT=0\n", "")

        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0]))

        cmd_host_run_puppet(mock_args_run_puppet)

        output = json.loads(captured[0])
        assert output["parameters"]["reason"] == "post-override puppet run"

    def test_writes_to_audit_log(
        self, mocker, mock_args_run_puppet: HostRunPuppetArgs, tmp_dir: Path
    ):
        """Writes result to audit log file."""
        audit_log = tmp_dir / "audit.jsonl"
        mock_args_run_puppet.audit_log = str(audit_log)
        mock_args_run_puppet.json = True

        mock_run_ssh = mocker.patch("fleetroll.commands.run_puppet.run_ssh")
        mock_run_ssh.return_value = (0, "EXIT=0\n", "")

        mocker.patch("builtins.print")

        cmd_host_run_puppet(mock_args_run_puppet)

        assert audit_log.exists()
        record = json.loads(audit_log.read_text().strip())
        assert record["action"] == "host.run_puppet"
        assert record["host"] == "test.example.com"

    def test_batch_parallel(self, mocker, mock_args_run_puppet: HostRunPuppetArgs, tmp_dir: Path):
        """Runs puppet on all hosts in a batch file."""
        hosts_file = tmp_dir / "staging.list"
        hosts_file.write_text("host1.example.com\nhost2.example.com\nhost3.example.com\n")
        mock_args_run_puppet.host = str(hosts_file)
        mock_args_run_puppet.audit_log = str(tmp_dir / "audit.jsonl")
        mock_args_run_puppet.json = False

        mock_run_ssh = mocker.patch("fleetroll.commands.run_puppet.run_ssh")
        mock_run_ssh.return_value = (0, "EXIT=0\n", "")

        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(str(a[0])))

        cmd_host_run_puppet(mock_args_run_puppet)

        assert mock_run_ssh.call_count == 3
        output = "\n".join(captured)
        assert "total=3" in output
        assert "successful=3" in output
        assert "failed=0" in output

    def test_no_audit_flag_skips_audit(
        self, mocker, mock_args_run_puppet: HostRunPuppetArgs, tmp_dir: Path
    ):
        """Does not call _run_auto_audit when --no-audit is set."""
        mock_args_run_puppet.audit_log = str(tmp_dir / "audit.jsonl")
        mock_args_run_puppet.no_audit = True

        mock_run_ssh = mocker.patch("fleetroll.commands.run_puppet.run_ssh")
        mock_run_ssh.return_value = (0, "EXIT=0\n", "")
        mock_audit = mocker.patch("fleetroll.commands._auto_audit._run_auto_audit")

        mocker.patch("builtins.print")

        cmd_host_run_puppet(mock_args_run_puppet)

        mock_audit.assert_not_called()

    def test_audit_called_by_default(
        self, mocker, mock_args_run_puppet: HostRunPuppetArgs, tmp_dir: Path
    ):
        """Calls _run_auto_audit when --no-audit is not set."""
        mock_args_run_puppet.audit_log = str(tmp_dir / "audit.jsonl")
        mock_args_run_puppet.no_audit = False

        mock_run_ssh = mocker.patch("fleetroll.commands.run_puppet.run_ssh")
        mock_run_ssh.return_value = (0, "EXIT=0\n", "")
        mock_audit = mocker.patch("fleetroll.commands._auto_audit._run_auto_audit")

        mocker.patch("builtins.print")

        cmd_host_run_puppet(mock_args_run_puppet)

        mock_audit.assert_called_once()

    def test_windows_hosts_filtered(
        self, mocker, mock_args_run_puppet: HostRunPuppetArgs, tmp_dir: Path
    ):
        """Windows hosts are skipped; no SSH is attempted for them."""
        hosts_file = tmp_dir / "staging.list"
        hosts_file.write_text("linux-host.example.com\nwin-host.wintest2.releng.mdc1.mozilla.com\n")
        mock_args_run_puppet.host = str(hosts_file)
        mock_args_run_puppet.audit_log = str(tmp_dir / "audit.jsonl")
        mock_args_run_puppet.json = False

        mock_run_ssh = mocker.patch("fleetroll.commands.run_puppet.run_ssh")
        mock_run_ssh.return_value = (0, "EXIT=0\n", "")

        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(str(a[0])))

        cmd_host_run_puppet(mock_args_run_puppet)

        assert mock_run_ssh.call_count == 1
        output = "\n".join(captured)
        assert "skipped=1" in output

    def test_staging_warning_emitted_for_non_staging(
        self, mocker, mock_args_run_puppet: HostRunPuppetArgs, tmp_dir: Path, capsys
    ):
        """Prints a warning to stderr when the host file lacks 'staging' in the name."""
        hosts_file = tmp_dir / "prod.list"
        hosts_file.write_text("host1.example.com\n")
        mock_args_run_puppet.host = str(hosts_file)
        mock_args_run_puppet.confirm = False
        mock_args_run_puppet.json = False

        mocker.patch("fleetroll.commands.run_puppet.run_ssh")
        mocker.patch("builtins.print")

        cmd_host_run_puppet(mock_args_run_puppet)

        stderr = capsys.readouterr().err
        assert "staging" in stderr.lower() or "WARNING" in stderr

    def test_no_staging_warning_for_staging_file(
        self, mocker, mock_args_run_puppet: HostRunPuppetArgs, tmp_dir: Path, capsys
    ):
        """No warning is emitted for host files that contain 'staging' in the name."""
        hosts_file = tmp_dir / "staging.list"
        hosts_file.write_text("host1.example.com\n")
        mock_args_run_puppet.host = str(hosts_file)
        mock_args_run_puppet.confirm = False
        mock_args_run_puppet.json = False

        mocker.patch("fleetroll.commands.run_puppet.run_ssh")
        mocker.patch("builtins.print")

        cmd_host_run_puppet(mock_args_run_puppet)

        stderr = capsys.readouterr().err
        assert "WARNING" not in stderr

    def test_audit_failure_does_not_raise(
        self, mocker, mock_args_run_puppet: HostRunPuppetArgs, tmp_dir: Path
    ):
        """Auto-audit failures are caught by _maybe_auto_audit and do not propagate."""
        mock_args_run_puppet.audit_log = str(tmp_dir / "audit.jsonl")
        mock_args_run_puppet.no_audit = False

        mock_run_ssh = mocker.patch("fleetroll.commands.run_puppet.run_ssh")
        mock_run_ssh.return_value = (0, "EXIT=0\n", "")

        mocker.patch(
            "fleetroll.commands._auto_audit._run_auto_audit",
            side_effect=RuntimeError("db unreachable"),
        )
        mocker.patch("builtins.print")

        # Should not raise even though audit failed
        cmd_host_run_puppet(mock_args_run_puppet)

    def test_dry_run_batch_shows_host_preview(
        self, mocker, mock_args_run_puppet: HostRunPuppetArgs, tmp_dir: Path
    ):
        """Dry-run batch mode shows hosts preview."""
        mock_args_run_puppet.confirm = False
        mock_args_run_puppet.json = False

        hosts_file = tmp_dir / "staging.list"
        hosts_file.write_text("host1.example.com\nhost2.example.com\n")
        mock_args_run_puppet.host = str(hosts_file)

        mock_run_ssh = mocker.patch("fleetroll.commands.run_puppet.run_ssh")
        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(str(a[0])))

        cmd_host_run_puppet(mock_args_run_puppet)

        mock_run_ssh.assert_not_called()
        output = "\n".join(captured)
        assert "Hosts file:" in output
        assert "host1.example.com" in output
        assert "host2.example.com" in output
