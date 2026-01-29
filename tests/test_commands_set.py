"""Tests for fleetroll/commands/set.py - set override command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fleetroll.cli_types import HostSetOverrideArgs
from fleetroll.commands.set import cmd_host_set
from fleetroll.exceptions import UserError


class TestCmdHostSet:
    """Tests for cmd_host_set function."""

    @pytest.fixture(autouse=True)
    def _disable_validation(self, mocker, request):
        if request.node.get_closest_marker("allow_validation"):
            return
        mocker.patch("fleetroll.commands.set.validate_override_syntax")

    def test_dry_run_without_confirm(
        self, mocker, mock_args_set: HostSetOverrideArgs, tmp_dir: Path
    ):
        """Prints summary and exits when --confirm not provided."""
        content_file = tmp_dir / "content.txt"
        content_file.write_text("dry run content")
        mock_args_set.from_file = str(content_file)
        mock_args_set.confirm = False
        mock_args_set.json = False
        mock_run_ssh = mocker.patch("fleetroll.commands.set.run_ssh")
        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0]))

        cmd_host_set(mock_args_set)

        mock_run_ssh.assert_not_called()
        assert any("DRY RUN" in line for line in captured)

    def test_requires_content_source(self, mock_args_set: HostSetOverrideArgs, tmp_dir: Path):
        """Raises UserError when --from-file missing."""
        mock_args_set.from_file = None
        mock_args_set.audit_log = str(tmp_dir / "audit.jsonl")
        with pytest.raises(UserError, match="--from-file"):
            cmd_host_set(mock_args_set)

    def test_successful_set_with_from_file(
        self, mocker, mock_args_set: HostSetOverrideArgs, tmp_dir: Path
    ):
        """Successfully sets override with --from-file."""
        content_file = tmp_dir / "content.txt"
        content_file.write_text("file content here")

        mock_args_set.from_file = str(content_file)
        mock_args_set.audit_log = str(tmp_dir / "audit.jsonl")
        mock_args_set.json = True

        mock_run_ssh = mocker.patch("fleetroll.commands.set.run_ssh")
        mock_run_ssh.return_value = (0, "", "")

        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0]))

        cmd_host_set(mock_args_set)

        # Verify content was read from file and passed to SSH
        call_kwargs = mock_run_ssh.call_args[1]
        assert call_kwargs["input_bytes"] == b"file content here"

    def test_includes_sha256_in_result(
        self, mocker, mock_args_set: HostSetOverrideArgs, tmp_dir: Path
    ):
        """Result includes SHA256 of content."""
        content_file = tmp_dir / "content.txt"
        content_file.write_text("test content")
        mock_args_set.audit_log = str(tmp_dir / "audit.jsonl")
        mock_args_set.json = True
        mock_args_set.from_file = str(content_file)

        mock_run_ssh = mocker.patch("fleetroll.commands.set.run_ssh")
        mock_run_ssh.return_value = (0, "", "")

        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0]))

        cmd_host_set(mock_args_set)

        output = json.loads(captured[0])
        assert "sha256" in output["parameters"]
        assert len(output["parameters"]["sha256"]) == 64

    def test_includes_backup_suffix(
        self, mocker, mock_args_set: HostSetOverrideArgs, tmp_dir: Path
    ):
        """Result includes backup suffix timestamp."""
        content_file = tmp_dir / "content.txt"
        content_file.write_text("backup content")
        mock_args_set.audit_log = str(tmp_dir / "audit.jsonl")
        mock_args_set.json = True
        mock_args_set.from_file = str(content_file)
        mock_args_set.no_backup = False

        mock_run_ssh = mocker.patch("fleetroll.commands.set.run_ssh")
        mock_run_ssh.return_value = (0, "", "")

        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0]))

        cmd_host_set(mock_args_set)

        output = json.loads(captured[0])
        assert output["parameters"]["backup"] is True
        assert "backup_suffix" in output["parameters"]

    def test_records_reason(self, mocker, mock_args_set: HostSetOverrideArgs, tmp_dir: Path):
        """Result includes reason when provided."""
        content_file = tmp_dir / "content.txt"
        content_file.write_text("reason content")
        mock_args_set.audit_log = str(tmp_dir / "audit.jsonl")
        mock_args_set.json = True
        mock_args_set.from_file = str(content_file)
        mock_args_set.reason = "testing the set command"

        mock_run_ssh = mocker.patch("fleetroll.commands.set.run_ssh")
        mock_run_ssh.return_value = (0, "", "")

        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0]))

        cmd_host_set(mock_args_set)

        output = json.loads(captured[0])
        assert output["parameters"]["reason"] == "testing the set command"

    def test_failed_ssh_command(self, mocker, mock_args_set: HostSetOverrideArgs, tmp_dir: Path):
        """Handles SSH command failure."""
        content_file = tmp_dir / "content.txt"
        content_file.write_text("failed content")
        mock_args_set.audit_log = str(tmp_dir / "audit.jsonl")
        mock_args_set.json = True
        mock_args_set.from_file = str(content_file)

        mock_run_ssh = mocker.patch("fleetroll.commands.set.run_ssh")
        mock_run_ssh.return_value = (1, "", "Permission denied")

        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0]))

        with pytest.raises(SystemExit) as excinfo:
            cmd_host_set(mock_args_set)
        assert excinfo.value.code == 1

        output = json.loads(captured[0])
        assert output["ok"] is False
        assert output["ssh_rc"] == 1

    def test_writes_to_audit_log(self, mocker, mock_args_set: HostSetOverrideArgs, tmp_dir: Path):
        """Writes result to audit log file."""
        content_file = tmp_dir / "content.txt"
        content_file.write_text("audit content")
        audit_log = tmp_dir / "audit.jsonl"
        mock_args_set.audit_log = str(audit_log)
        mock_args_set.json = True
        mock_args_set.from_file = str(content_file)

        mock_run_ssh = mocker.patch("fleetroll.commands.set.run_ssh")
        mock_run_ssh.return_value = (0, "", "")

        mocker.patch("builtins.print")

        cmd_host_set(mock_args_set)

        assert audit_log.exists()
        record = json.loads(audit_log.read_text().strip())
        assert record["action"] == "host.set_override"

    @pytest.mark.allow_validation
    def test_validation_failure(self, mocker, mock_args_set: HostSetOverrideArgs, tmp_dir: Path):
        """Validation errors raise UserError."""
        import shutil

        if not shutil.which("bash"):
            pytest.skip("bash not available for syntax validation test")

        bad_file = tmp_dir / "bad_override.sh"
        bad_file.write_text("PUPPET_REPO='https://example.com\n")

        mock_args_set.audit_log = str(tmp_dir / "audit.jsonl")
        mock_args_set.from_file = str(bad_file)
        mock_args_set.validate = True

        mocker.patch("fleetroll.commands.set.run_ssh", side_effect=AssertionError())

        with pytest.raises(UserError, match="validation failed"):
            cmd_host_set(mock_args_set)
