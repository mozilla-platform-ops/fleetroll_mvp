"""Tests for fleetroll/commands/audit.py - audit command behavior."""

from __future__ import annotations

import json

import pytest
from fleetroll.cli_types import HostAuditArgs
from fleetroll.commands.audit import aggregate_audit_summary, cmd_host_audit
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


class TestAggregateAuditSummary:
    """Tests for aggregate_audit_summary helper function."""

    def test_aggregates_successful_results(self):
        """Should count successful and failed results."""
        hosts = ["host1", "host2", "host3"]
        results = [
            {"host": "host1", "ok": True, "observed": {}},
            {"host": "host2", "ok": True, "observed": {}},
            {"host": "host3", "ok": False, "error": "Connection failed"},
        ]

        summary = aggregate_audit_summary(results, hosts)

        assert summary["total"] == 3
        assert summary["successful"] == 2
        assert summary["failed"] == 1
        assert summary["results"] == results

    def test_collects_unique_overrides(self):
        """Should collect unique overrides by SHA256."""
        hosts = ["host1", "host2", "host3"]
        results = [
            {
                "host": "host1",
                "ok": True,
                "observed": {
                    "override_present": True,
                    "override_sha256": "abc123",
                    "override_contents_for_display": "content1",
                },
            },
            {
                "host": "host2",
                "ok": True,
                "observed": {
                    "override_present": True,
                    "override_sha256": "abc123",
                    "override_contents_for_display": "content1",
                },
            },
            {
                "host": "host3",
                "ok": True,
                "observed": {
                    "override_present": True,
                    "override_sha256": "def456",
                    "override_contents_for_display": "content2",
                },
            },
        ]

        summary = aggregate_audit_summary(results, hosts)

        assert len(summary["unique_overrides"]) == 2
        assert "abc123" in summary["unique_overrides"]
        assert "def456" in summary["unique_overrides"]
        assert summary["unique_overrides"]["abc123"][0] == "content1"
        assert summary["unique_overrides"]["abc123"][1] == ["host1", "host2"]
        assert summary["unique_overrides"]["def456"][1] == ["host3"]

    def test_ignores_results_without_overrides(self):
        """Should not include results without overrides."""
        hosts = ["host1", "host2"]
        results = [
            {"host": "host1", "ok": True, "observed": {"override_present": False}},
            {"host": "host2", "ok": True, "observed": {}},
        ]

        summary = aggregate_audit_summary(results, hosts)

        assert summary["unique_overrides"] == {}

    def test_ignores_failed_results(self):
        """Should not include overrides from failed results."""
        hosts = ["host1"]
        results = [
            {
                "host": "host1",
                "ok": False,
                "observed": {
                    "override_present": True,
                    "override_sha256": "abc123",
                    "override_contents_for_display": "content",
                },
            }
        ]

        summary = aggregate_audit_summary(results, hosts)

        assert summary["unique_overrides"] == {}

    def test_handles_empty_results(self):
        """Should handle empty results list."""
        hosts = []
        results = []

        summary = aggregate_audit_summary(results, hosts)

        assert summary["total"] == 0
        assert summary["successful"] == 0
        assert summary["failed"] == 0
        assert summary["unique_overrides"] == {}

    def test_ignores_overrides_missing_sha_or_content(self):
        """Should skip overrides without SHA or content."""
        hosts = ["host1", "host2"]
        results = [
            {
                "host": "host1",
                "ok": True,
                "observed": {
                    "override_present": True,
                    "override_sha256": None,
                    "override_contents_for_display": "content",
                },
            },
            {
                "host": "host2",
                "ok": True,
                "observed": {
                    "override_present": True,
                    "override_sha256": "abc123",
                    "override_contents_for_display": "",
                },
            },
        ]

        summary = aggregate_audit_summary(results, hosts)

        assert summary["unique_overrides"] == {}
