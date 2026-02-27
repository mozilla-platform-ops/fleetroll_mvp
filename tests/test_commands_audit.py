"""Tests for fleetroll/commands/audit.py - audit command behavior."""

from __future__ import annotations

import json

import pytest
from fleetroll.cli_types import HostAuditArgs
from fleetroll.commands.audit import (
    aggregate_audit_summary,
    cmd_host_audit,
    format_single_host_output,
    format_summary_table,
)
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


# ---------------------------------------------------------------------------
# format_summary_table
# ---------------------------------------------------------------------------


def _make_summary(
    results: list,
    unique_overrides: dict | None = None,
) -> dict:
    """Build a summary dict as aggregate_audit_summary would produce."""
    total = len(results)
    successful = sum(1 for r in results if r.get("ok"))
    failed = total - successful
    return {
        "results": results,
        "total": total,
        "successful": successful,
        "failed": failed,
        "unique_overrides": unique_overrides or {},
    }


class TestFormatSummaryTable:
    """Tests for format_summary_table pure function."""

    def test_all_success_no_failures(self):
        """All-success summary shows counts and no failure breakdown."""
        results = [
            {"host": "host1", "ok": True, "observed": {}},
            {"host": "host2", "ok": True, "observed": {}},
        ]
        output = format_summary_table(_make_summary(results))
        assert "Total hosts:        2" in output
        assert "Successful:         2" in output
        assert "Failed:             0" in output
        # No failure breakdown lines when failed == 0
        assert "Connection:" not in output

    def test_mixed_success_failure(self):
        """Mixed results show failure counts and per-host status."""
        results = [
            {"host": "host1", "ok": True, "observed": {}},
            {"host": "host2", "ok": False, "error": "timeout", "stderr": ""},
        ]
        output = format_summary_table(_make_summary(results))
        assert "Failed:             1" in output
        assert "Connection:     1" in output
        assert "✓ host1" in output
        assert "✗ host2" in output

    def test_connection_vs_other_failures(self):
        """Connection failures (timeout/connection) separated from other failures."""
        results = [
            {"host": "host1", "ok": False, "error": "timeout", "stderr": ""},
            {"host": "host2", "ok": False, "error": "permission denied", "stderr": ""},
        ]
        output = format_summary_table(_make_summary(results))
        assert "Connection:     1" in output
        assert "Other:          1" in output

    def test_override_present(self):
        """Hosts with override show OVERRIDE in output."""
        results = [
            {
                "host": "host1",
                "ok": True,
                "observed": {
                    "override_present": True,
                    "override_sha256": "test_override_sha_xyzw",
                },
            }
        ]
        output = format_summary_table(_make_summary(results))
        assert "OVERRIDE" in output
        assert "test_overrid" in output

    def test_verbose_shows_role_and_meta(self):
        """Verbose mode includes role and override metadata."""
        results = [
            {
                "host": "host1",
                "ok": True,
                "attempts": 2,
                "observed": {
                    "role": "gecko_t_linux",
                    "override_present": True,
                    "override_sha256": "test_override_sha_xyzw",
                    "override_meta": {"mode": "0644", "owner": "root", "size": 42},
                },
            }
        ]
        output = format_summary_table(_make_summary(results), verbose=True)
        assert "gecko_t_linux" in output
        assert "mode=0644" in output
        assert "Attempts: 2" in output

    def test_unique_overrides_non_verbose(self):
        """Non-verbose mode shows inline content for unique overrides."""
        sha = "a" * 64
        results = [{"host": "host1", "ok": True, "observed": {}}]
        summary = _make_summary(results, unique_overrides={sha: ("key: value\n", ["host1"])})
        output = format_summary_table(summary, verbose=False)
        assert "Unique Override Contents" in output
        assert sha[:12] in output
        assert "key: value" in output
        assert "content_start" in output

    def test_unique_overrides_verbose(self):
        """Verbose mode shows stored path instead of inline content."""
        sha = "b" * 64
        results = [{"host": "host1", "ok": True, "observed": {}}]
        summary = _make_summary(results, unique_overrides={sha: ("key: value\n", ["host1"])})
        output = format_summary_table(summary, verbose=True)
        assert "Unique Override Contents" in output
        assert "Stored:" in output
        assert "content_start" not in output

    def test_hosts_sorted_alphabetically(self):
        """Hosts in the status table are sorted alphabetically."""
        results = [
            {"host": "zebra", "ok": True, "observed": {}},
            {"host": "alpha", "ok": True, "observed": {}},
        ]
        output = format_summary_table(_make_summary(results))
        assert output.index("alpha") < output.index("zebra")


# ---------------------------------------------------------------------------
# format_single_host_output
# ---------------------------------------------------------------------------


def _make_no_content_args():
    """Return minimal HostAuditArgs-like object with no_content=False."""
    args = HostAuditArgs.__new__(HostAuditArgs)
    args.no_content = False
    return args


class TestFormatSingleHostOutput:
    """Tests for format_single_host_output (prints to stdout)."""

    def test_failed_audit(self, capsys):
        """Failed result prints FAILED status."""
        result = {"host": "host1", "ok": False, "error": "timeout", "stderr": "timed out"}
        format_single_host_output(result, _make_no_content_args())
        out = capsys.readouterr().out
        assert "Host: host1" in out
        assert "FAILED" in out
        assert "timeout" in out

    def test_failed_shows_stderr(self, capsys):
        """Failed result with stderr prints it."""
        result = {"host": "host1", "ok": False, "error": "ssh_error", "stderr": "Permission denied"}
        format_single_host_output(result, _make_no_content_args())
        out = capsys.readouterr().out
        assert "Permission denied" in out

    def test_success_no_override(self, capsys):
        """Success with no override prints NOT PRESENT."""
        result = {
            "host": "host1",
            "ok": True,
            "observed": {"override_present": False, "role_present": True, "role": "gecko_t_linux"},
        }
        format_single_host_output(result, _make_no_content_args())
        out = capsys.readouterr().out
        assert "Host: host1" in out
        assert "Override: NOT PRESENT" in out
        assert "Role: gecko_t_linux" in out

    def test_success_with_override(self, capsys):
        """Success with override shows metadata and hash."""
        result = {
            "host": "host1",
            "ok": True,
            "observed": {
                "override_present": True,
                "override_sha256": "test_override_sha_abcd",
                "override_meta": {
                    "mode": "0644",
                    "owner": "root",
                    "group": "root",
                    "size": 42,
                    "mtime_epoch": 0,
                },
                "role_present": False,
            },
        }
        format_single_host_output(result, _make_no_content_args())
        out = capsys.readouterr().out
        assert "Override: PRESENT" in out
        assert "mode=0644" in out
        assert "test_override_sha_abcd" in out

    def test_no_content_flag_suppresses_body(self, capsys):
        """--no-content suppresses override body."""
        args = _make_no_content_args()
        args.no_content = True
        result = {
            "host": "host1",
            "ok": True,
            "observed": {
                "override_present": True,
                "override_sha256": "abc",
                "override_meta": {},
                "override_contents_for_display": "secret content",
                "role_present": False,
            },
        }
        format_single_host_output(result, args)
        out = capsys.readouterr().out
        assert "suppressed" in out
        assert "secret content" not in out

    def test_vault_present(self, capsys):
        """Vault PRESENT shows metadata."""
        result = {
            "host": "host1",
            "ok": True,
            "observed": {
                "override_present": False,
                "vault_present": True,
                "vault_sha256": "vlt123",
                "vault_meta": {
                    "mode": "0640",
                    "owner": "root",
                    "group": "wheel",
                    "size": 10,
                    "mtime_epoch": 0,
                },
                "role_present": False,
            },
        }
        format_single_host_output(result, _make_no_content_args())
        out = capsys.readouterr().out
        assert "Vault: PRESENT" in out
        assert "mode=0640" in out

    def test_vault_not_present(self, capsys):
        """Vault absent shows NOT PRESENT."""
        result = {
            "host": "host1",
            "ok": True,
            "observed": {"override_present": False, "vault_present": False, "role_present": False},
        }
        format_single_host_output(result, _make_no_content_args())
        out = capsys.readouterr().out
        assert "Vault: NOT PRESENT" in out

    def test_vault_unknown(self, capsys):
        """vault_present=None shows UNKNOWN."""
        result = {
            "host": "host1",
            "ok": True,
            "observed": {"override_present": False, "vault_present": None, "role_present": False},
        }
        format_single_host_output(result, _make_no_content_args())
        out = capsys.readouterr().out
        assert "UNKNOWN" in out

    def test_role_missing(self, capsys):
        """role_present=False shows missing message."""
        result = {
            "host": "host1",
            "ok": True,
            "observed": {"override_present": False, "role_present": False},
        }
        format_single_host_output(result, _make_no_content_args())
        out = capsys.readouterr().out
        assert "missing or unreadable" in out
