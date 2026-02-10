"""Tests for fleetroll/commands/unset.py - unset override command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fleetroll.cli_types import HostUnsetOverrideArgs
from fleetroll.commands.unset import cmd_host_unset
from fleetroll.exceptions import CommandFailureError


class TestCmdHostUnset:
    """Tests for cmd_host_unset function."""

    def test_dry_run_without_confirm(self, mocker, mock_args_unset: HostUnsetOverrideArgs):
        """Prints summary and exits when --confirm not provided."""
        mock_args_unset.confirm = False
        mock_args_unset.json = False
        mock_run_ssh = mocker.patch("fleetroll.commands.unset.run_ssh")
        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0]))

        cmd_host_unset(mock_args_unset)

        mock_run_ssh.assert_not_called()
        assert any("DRY RUN" in line for line in captured)

    def test_successful_unset_removed(
        self, mocker, mock_args_unset: HostUnsetOverrideArgs, tmp_dir: Path
    ):
        """Successfully unsets existing override."""
        mock_args_unset.audit_log = str(tmp_dir / "audit.jsonl")
        mock_args_unset.json = True

        mock_run_ssh = mocker.patch("fleetroll.commands.unset.run_ssh")
        mock_run_ssh.return_value = (0, "REMOVED=1\n", "")

        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0]))

        cmd_host_unset(mock_args_unset)

        output = json.loads(captured[0])
        assert output["ok"] is True
        assert output["action"] == "host.unset_override"
        assert output["observed"]["removed"] is True

    def test_successful_unset_not_present(
        self, mocker, mock_args_unset: HostUnsetOverrideArgs, tmp_dir: Path
    ):
        """Handles case where override doesn't exist."""
        mock_args_unset.audit_log = str(tmp_dir / "audit.jsonl")
        mock_args_unset.json = True

        mock_run_ssh = mocker.patch("fleetroll.commands.unset.run_ssh")
        mock_run_ssh.return_value = (0, "REMOVED=0\n", "")

        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0]))

        cmd_host_unset(mock_args_unset)

        output = json.loads(captured[0])
        assert output["ok"] is True
        assert output["observed"]["removed"] is False

    def test_includes_backup_info(
        self, mocker, mock_args_unset: HostUnsetOverrideArgs, tmp_dir: Path
    ):
        """Result includes backup information."""
        mock_args_unset.audit_log = str(tmp_dir / "audit.jsonl")
        mock_args_unset.json = True
        mock_args_unset.no_backup = False

        mock_run_ssh = mocker.patch("fleetroll.commands.unset.run_ssh")
        mock_run_ssh.return_value = (0, "REMOVED=1\n", "")

        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0]))

        cmd_host_unset(mock_args_unset)

        output = json.loads(captured[0])
        assert output["observed"]["backup"] is True
        assert "backup_suffix" in output["observed"]

    def test_no_backup_flag(self, mocker, mock_args_unset: HostUnsetOverrideArgs, tmp_dir: Path):
        """Respects --no-backup flag."""
        mock_args_unset.audit_log = str(tmp_dir / "audit.jsonl")
        mock_args_unset.json = True
        mock_args_unset.no_backup = True

        mock_run_ssh = mocker.patch("fleetroll.commands.unset.run_ssh")
        mock_run_ssh.return_value = (0, "REMOVED=1\n", "")

        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0]))

        cmd_host_unset(mock_args_unset)

        output = json.loads(captured[0])
        assert output["observed"]["backup"] is False

    def test_records_reason(self, mocker, mock_args_unset: HostUnsetOverrideArgs, tmp_dir: Path):
        """Result includes reason when provided."""
        mock_args_unset.audit_log = str(tmp_dir / "audit.jsonl")
        mock_args_unset.json = True
        mock_args_unset.reason = "cleaning up test override"

        mock_run_ssh = mocker.patch("fleetroll.commands.unset.run_ssh")
        mock_run_ssh.return_value = (0, "REMOVED=1\n", "")

        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0]))

        cmd_host_unset(mock_args_unset)

        output = json.loads(captured[0])
        assert output["parameters"]["reason"] == "cleaning up test override"

    def test_failed_ssh_command(
        self, mocker, mock_args_unset: HostUnsetOverrideArgs, tmp_dir: Path
    ):
        """Handles SSH command failure."""
        mock_args_unset.audit_log = str(tmp_dir / "audit.jsonl")
        mock_args_unset.json = True

        mock_run_ssh = mocker.patch("fleetroll.commands.unset.run_ssh")
        mock_run_ssh.return_value = (255, "", "Connection refused")

        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0]))

        with pytest.raises(CommandFailureError) as excinfo:
            cmd_host_unset(mock_args_unset)
        assert excinfo.value.rc == 1

        output = json.loads(captured[0])
        assert output["ok"] is False
        assert output["ssh_rc"] == 255

    def test_writes_to_audit_log(
        self, mocker, mock_args_unset: HostUnsetOverrideArgs, tmp_dir: Path
    ):
        """Writes result to audit log file."""
        audit_log = tmp_dir / "audit.jsonl"
        mock_args_unset.audit_log = str(audit_log)
        mock_args_unset.json = True

        mock_run_ssh = mocker.patch("fleetroll.commands.unset.run_ssh")
        mock_run_ssh.return_value = (0, "REMOVED=1\n", "")

        mocker.patch("builtins.print")

        cmd_host_unset(mock_args_unset)

        assert audit_log.exists()
        record = json.loads(audit_log.read_text().strip())
        assert record["action"] == "host.unset_override"
        assert record["host"] == "test.example.com"

    def test_dry_run_batch_shows_host_preview_small_list(
        self, mocker, mock_args_unset: HostUnsetOverrideArgs, tmp_dir: Path
    ):
        """Dry-run batch mode shows all hosts when count <= 5."""
        mock_args_unset.confirm = False
        mock_args_unset.json = False

        hosts_file = tmp_dir / "hosts.txt"
        hosts_file.write_text("host1.example.com\nhost2.example.com\nhost3.example.com\n")
        mock_args_unset.host = str(hosts_file)

        mock_run_ssh = mocker.patch("fleetroll.commands.unset.run_ssh")
        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(str(a[0])))

        cmd_host_unset(mock_args_unset)

        mock_run_ssh.assert_not_called()
        output = "\n".join(captured)
        assert "Hosts file:" in output
        assert " 3" in output
        assert "  - host1.example.com" in output
        assert "  - host2.example.com" in output
        assert "  - host3.example.com" in output
        assert "more host" not in output

    def test_dry_run_batch_shows_host_preview_large_list(
        self, mocker, mock_args_unset: HostUnsetOverrideArgs, tmp_dir: Path
    ):
        """Dry-run batch mode shows first 5 hosts + overflow message when count > 5."""
        mock_args_unset.confirm = False
        mock_args_unset.json = False

        hosts_file = tmp_dir / "hosts.txt"
        hosts = [f"host{i}.example.com" for i in range(1, 11)]
        hosts_file.write_text("\n".join(hosts) + "\n")
        mock_args_unset.host = str(hosts_file)

        mock_run_ssh = mocker.patch("fleetroll.commands.unset.run_ssh")
        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(str(a[0])))

        cmd_host_unset(mock_args_unset)

        mock_run_ssh.assert_not_called()
        output = "\n".join(captured)
        assert "Hosts file:" in output
        assert " 10" in output
        assert "  - host1.example.com" in output
        assert "  - host2.example.com" in output
        assert "  - host3.example.com" in output
        assert "  - host4.example.com" in output
        assert "  - host5.example.com" in output
        assert "  - host6.example.com" not in output
        assert "5 more hosts" in output
