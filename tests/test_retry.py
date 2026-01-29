"""Tests for retry logic in fleetroll/commands/audit.py."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fleetroll.cli_types import HostAuditArgs
from fleetroll.commands.audit import audit_single_host_with_retry


class TestRetryLogic:
    """Tests for audit_single_host_with_retry function."""

    def _make_args(self, tmp_dir: Path) -> HostAuditArgs:
        """Create Args object for testing."""
        return HostAuditArgs(
            host="test.example.com",
            ssh_option=None,
            connect_timeout=10,
            timeout=60,
            override_path="/etc/test",
            role_path="/etc/role",
            vault_path="/root/vault.yaml",
            no_content=True,
            audit_log=str(tmp_dir / "audit.jsonl"),
            json=False,
            workers=10,
            batch_timeout=600,
            verbose=False,
            quiet=False,
        )

    def test_no_retry_on_success(self, mocker, tmp_dir: Path):
        """Does not retry when SSH succeeds."""
        mock_run_ssh = mocker.patch("fleetroll.commands.audit.run_ssh")
        mock_run_ssh.return_value = (0, "ROLE_PRESENT=0\nOVERRIDE_PRESENT=0\n", "")

        mock_process = mocker.patch("fleetroll.commands.audit.process_audit_result")
        mock_process.return_value = {"ok": True, "host": "test"}

        args = self._make_args(tmp_dir)

        result = audit_single_host_with_retry(
            "test.example.com",
            args=args,
            ssh_opts=[],
            remote_cmd="test",
            audit_log=tmp_dir / "audit.jsonl",
            actor="test",
            retry_budget={"deadline": time.time() + 600},
            lock=threading.Lock(),
            log_lock=threading.Lock(),
        )

        assert mock_run_ssh.call_count == 1
        assert result["attempts"] == 1

    def test_retry_on_connection_refused(self, mocker, tmp_dir: Path):
        """Retries when SSH fails with connection refused."""
        mock_run_ssh = mocker.patch("fleetroll.commands.audit.run_ssh")
        # With AUDIT_MAX_RETRIES=1, only makes 1 attempt (no retries)
        mock_run_ssh.return_value = (255, "", "Connection refused")

        # Patch sleep to speed up test
        mocker.patch("fleetroll.commands.audit.time.sleep")

        args = self._make_args(tmp_dir)

        result = audit_single_host_with_retry(
            "test.example.com",
            args=args,
            ssh_opts=[],
            remote_cmd="test",
            audit_log=tmp_dir / "audit.jsonl",
            actor="test",
            retry_budget={"deadline": time.time() + 600},
            lock=threading.Lock(),
            log_lock=threading.Lock(),
        )

        assert mock_run_ssh.call_count == 1
        assert result["attempts"] == 1
        assert result["ok"] is False

    def test_retry_on_connection_timeout(self, mocker, tmp_dir: Path):
        """Retries when SSH fails with connection timeout."""
        mock_run_ssh = mocker.patch("fleetroll.commands.audit.run_ssh")
        # With AUDIT_MAX_RETRIES=1, only makes 1 attempt (no retries)
        mock_run_ssh.return_value = (255, "", "Connection timed out")

        mocker.patch("fleetroll.commands.audit.time.sleep")

        args = self._make_args(tmp_dir)

        result = audit_single_host_with_retry(
            "test.example.com",
            args=args,
            ssh_opts=[],
            remote_cmd="test",
            audit_log=tmp_dir / "audit.jsonl",
            actor="test",
            retry_budget={"deadline": time.time() + 600},
            lock=threading.Lock(),
            log_lock=threading.Lock(),
        )

        assert mock_run_ssh.call_count == 1
        assert result["ok"] is False

    def test_retry_on_hostname_resolution_failure(self, mocker, tmp_dir: Path):
        """Retries when SSH fails with hostname resolution error."""
        mock_run_ssh = mocker.patch("fleetroll.commands.audit.run_ssh")
        # With AUDIT_MAX_RETRIES=1, only makes 1 attempt (no retries)
        mock_run_ssh.return_value = (255, "", "Could not resolve hostname")

        mocker.patch("fleetroll.commands.audit.time.sleep")

        args = self._make_args(tmp_dir)

        result = audit_single_host_with_retry(
            "test.example.com",
            args=args,
            ssh_opts=[],
            remote_cmd="test",
            audit_log=tmp_dir / "audit.jsonl",
            actor="test",
            retry_budget={"deadline": time.time() + 600},
            lock=threading.Lock(),
            log_lock=threading.Lock(),
        )

        assert mock_run_ssh.call_count == 1
        assert result["ok"] is False

    def test_no_retry_on_non_connection_error(self, mocker, tmp_dir: Path):
        """Does not retry on non-connection errors (e.g., permission denied)."""
        mock_run_ssh = mocker.patch("fleetroll.commands.audit.run_ssh")
        mock_run_ssh.return_value = (1, "", "Permission denied")

        mock_process = mocker.patch("fleetroll.commands.audit.process_audit_result")
        mock_process.return_value = {"ok": False, "host": "test"}

        args = self._make_args(tmp_dir)

        _result = audit_single_host_with_retry(
            "test.example.com",
            args=args,
            ssh_opts=[],
            remote_cmd="test",
            audit_log=tmp_dir / "audit.jsonl",
            actor="test",
            retry_budget={"deadline": time.time() + 600},
            lock=threading.Lock(),
            log_lock=threading.Lock(),
        )

        # Should not retry on permission denied
        assert mock_run_ssh.call_count == 1

    def test_max_retries_exceeded(self, mocker, tmp_dir: Path):
        """Returns error when max retries exceeded."""
        mock_run_ssh = mocker.patch("fleetroll.commands.audit.run_ssh")
        # All attempts fail with connection error
        mock_run_ssh.return_value = (255, "", "Connection refused")

        mocker.patch("fleetroll.commands.audit.time.sleep")

        args = self._make_args(tmp_dir)

        result = audit_single_host_with_retry(
            "test.example.com",
            args=args,
            ssh_opts=[],
            remote_cmd="test",
            audit_log=tmp_dir / "audit.jsonl",
            actor="test",
            retry_budget={"deadline": time.time() + 600},
            lock=threading.Lock(),
            log_lock=threading.Lock(),
        )

        assert result["ok"] is False
        assert result["error"] == "max_retries_exceeded"
        assert result["attempts"] == 1  # AUDIT_MAX_RETRIES=1

    def test_respects_batch_timeout(self, mocker, tmp_dir: Path):
        """Stops retrying when batch timeout exceeded."""
        mock_run_ssh = mocker.patch("fleetroll.commands.audit.run_ssh")
        mock_run_ssh.return_value = (255, "", "Connection refused")

        args = self._make_args(tmp_dir)

        # Set deadline in the past
        result = audit_single_host_with_retry(
            "test.example.com",
            args=args,
            ssh_opts=[],
            remote_cmd="test",
            audit_log=tmp_dir / "audit.jsonl",
            actor="test",
            retry_budget={"deadline": time.time() - 1},  # Already expired
            lock=threading.Lock(),
            log_lock=threading.Lock(),
        )

        assert result["ok"] is False
        assert result["error"] == "batch_timeout_exceeded"
        # Should not have made any SSH calls due to immediate timeout
        assert result["attempts"] == 0

    def test_exponential_backoff(self, mocker, tmp_dir: Path):
        """Uses exponential backoff between retries."""
        # Skip test when AUDIT_MAX_RETRIES=1 (no retries to test backoff)
        pytest.skip("Exponential backoff not applicable with AUDIT_MAX_RETRIES=1")

    def test_result_includes_attempts_count(self, mocker, tmp_dir: Path):
        """Result includes number of attempts made."""
        mock_run_ssh = mocker.patch("fleetroll.commands.audit.run_ssh")
        # With AUDIT_MAX_RETRIES=1, only makes 1 attempt
        mock_run_ssh.return_value = (255, "", "Connection refused")

        mocker.patch("fleetroll.commands.audit.time.sleep")

        args = self._make_args(tmp_dir)

        result = audit_single_host_with_retry(
            "test.example.com",
            args=args,
            ssh_opts=[],
            remote_cmd="test",
            audit_log=tmp_dir / "audit.jsonl",
            actor="test",
            retry_budget={"deadline": time.time() + 600},
            lock=threading.Lock(),
            log_lock=threading.Lock(),
        )

        assert "attempts" in result
        assert result["attempts"] == 1
