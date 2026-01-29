"""Tests for fleetroll/commands/audit.py - audit command behavior."""

from __future__ import annotations

import json

import pytest
from fleetroll.cli_types import HostAuditArgs
from fleetroll.commands.audit import cmd_host_audit
from fleetroll.exceptions import CommandFailureError


class TestCmdHostAudit:
    """Tests for cmd_host_audit JSON exit codes."""

    def test_json_single_host_failure_exits_nonzero(
        self, mocker, mock_args_audit: HostAuditArgs, tmp_dir, capsys
    ):
        """JSON single-host audit raises CommandFailureError on failure."""
        mock_args_audit.json = True
        mock_args_audit.audit_log = str(tmp_dir / "audit.jsonl")

        mock_run_ssh = mocker.patch("fleetroll.commands.audit.run_ssh")
        mock_run_ssh.return_value = (1, "", "Permission denied")

        with pytest.raises(CommandFailureError) as excinfo:
            cmd_host_audit(mock_args_audit)
        assert excinfo.value.rc == 1

        output = json.loads(capsys.readouterr().out)
        assert output["ok"] is False

    def test_json_batch_failure_exits_nonzero(
        self, mocker, mock_args_audit: HostAuditArgs, tmp_dir, tmp_host_file, capsys
    ):
        """JSON batch audit raises CommandFailureError when any host fails."""
        mock_args_audit.json = True
        mock_args_audit.audit_log = str(tmp_dir / "audit.jsonl")
        mock_args_audit.host = str(tmp_host_file)
        mock_args_audit.workers = 2

        mock_run_ssh = mocker.patch("fleetroll.commands.audit.run_ssh")
        mock_run_ssh.return_value = (1, "", "Permission denied")

        with pytest.raises(CommandFailureError) as excinfo:
            cmd_host_audit(mock_args_audit)
        assert excinfo.value.rc == 1

        output = json.loads(capsys.readouterr().out)
        assert output["failed"] > 0
