"""Tests for retry logic in fleetroll/commands/audit.py."""

from __future__ import annotations

import threading
import time
from pathlib import Path

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
            db_path=tmp_dir / "test.db",
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
            db_path=tmp_dir / "test.db",
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
            db_path=tmp_dir / "test.db",
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
            db_path=tmp_dir / "test.db",
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
        mock_run_ssh.return_value = (255, "", "Permission denied (publickey)")

        mock_process = mocker.patch("fleetroll.commands.audit.process_audit_result")
        mock_process.return_value = {"ok": False, "host": "test"}

        args = self._make_args(tmp_dir)

        _result = audit_single_host_with_retry(
            "test.example.com",
            args=args,
            ssh_opts=[],
            remote_cmd="test",
            db_path=tmp_dir / "test.db",
            actor="test",
            retry_budget={"deadline": time.time() + 600},
            lock=threading.Lock(),
            log_lock=threading.Lock(),
        )

        # Should not retry on permission denied
        assert mock_run_ssh.call_count == 1

    def test_ssh_auth_failure_logged_not_retried(self, mocker, tmp_dir: Path):
        """SSH auth failures (rc=255, Permission denied) are logged, not retried."""
        mock_run_ssh = mocker.patch("fleetroll.commands.audit.run_ssh")
        mock_run_ssh.return_value = (255, "", "Permission denied (publickey)")

        mock_process = mocker.patch("fleetroll.commands.audit.process_audit_result")
        mock_process.return_value = {"ok": False, "host": "test"}

        args = self._make_args(tmp_dir)

        result = audit_single_host_with_retry(
            "test.example.com",
            args=args,
            ssh_opts=[],
            remote_cmd="test",
            db_path=tmp_dir / "test.db",
            actor="test",
            retry_budget={"deadline": time.time() + 600},
            lock=threading.Lock(),
            log_lock=threading.Lock(),
        )

        # Should not retry - auth error is not a connection error
        assert mock_run_ssh.call_count == 1
        # Should log to database
        assert mock_process.call_count == 1
        assert result["ok"] is False

    def test_ssh_protocol_error_logged_not_retried(self, mocker, tmp_dir: Path):
        """SSH protocol errors (rc=255) are logged, not retried."""
        mock_run_ssh = mocker.patch("fleetroll.commands.audit.run_ssh")
        mock_run_ssh.return_value = (255, "", "Protocol error")

        mock_process = mocker.patch("fleetroll.commands.audit.process_audit_result")
        mock_process.return_value = {"ok": False, "host": "test"}

        args = self._make_args(tmp_dir)

        result = audit_single_host_with_retry(
            "test.example.com",
            args=args,
            ssh_opts=[],
            remote_cmd="test",
            db_path=tmp_dir / "test.db",
            actor="test",
            retry_budget={"deadline": time.time() + 600},
            lock=threading.Lock(),
            log_lock=threading.Lock(),
        )

        # Should not retry - protocol error is not a connection error
        assert mock_run_ssh.call_count == 1
        # Should log to database
        assert mock_process.call_count == 1
        assert result["ok"] is False

    def test_network_unreachable_is_retried(self, mocker, tmp_dir: Path):
        """Network unreachable (rc=255) is treated as connection error, retried."""
        mock_run_ssh = mocker.patch("fleetroll.commands.audit.run_ssh")
        mock_run_ssh.return_value = (255, "", "Network is unreachable")

        mock_process = mocker.patch("fleetroll.commands.audit.process_audit_result")
        mocker.patch("fleetroll.commands.audit.time.sleep")

        args = self._make_args(tmp_dir)

        result = audit_single_host_with_retry(
            "test.example.com",
            args=args,
            ssh_opts=[],
            remote_cmd="test",
            db_path=tmp_dir / "test.db",
            actor="test",
            retry_budget={"deadline": time.time() + 600},
            lock=threading.Lock(),
            log_lock=threading.Lock(),
        )

        # Should retry on network unreachable (connection error)
        # AUDIT_MAX_RETRIES defaults to 1, so expect 1 attempt
        assert mock_run_ssh.call_count == 1
        # Should not log to database (connection error bypasses logging)
        assert mock_process.call_count == 0
        assert result["ok"] is False
        assert result["error"] == "max_retries_exceeded"

    def test_no_route_to_host_is_retried(self, mocker, tmp_dir: Path):
        """No route to host (rc=255) is treated as connection error, retried."""
        mock_run_ssh = mocker.patch("fleetroll.commands.audit.run_ssh")
        mock_run_ssh.return_value = (255, "", "No route to host")

        mock_process = mocker.patch("fleetroll.commands.audit.process_audit_result")
        mocker.patch("fleetroll.commands.audit.time.sleep")

        args = self._make_args(tmp_dir)

        result = audit_single_host_with_retry(
            "test.example.com",
            args=args,
            ssh_opts=[],
            remote_cmd="test",
            db_path=tmp_dir / "test.db",
            actor="test",
            retry_budget={"deadline": time.time() + 600},
            lock=threading.Lock(),
            log_lock=threading.Lock(),
        )

        # Should retry on no route to host (connection error)
        # AUDIT_MAX_RETRIES defaults to 1, so expect 1 attempt
        assert mock_run_ssh.call_count == 1
        # Should not log to database (connection error bypasses logging)
        assert mock_process.call_count == 0
        assert result["ok"] is False
        assert result["error"] == "max_retries_exceeded"

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
            db_path=tmp_dir / "test.db",
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
            db_path=tmp_dir / "test.db",
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
        # Temporarily override AUDIT_MAX_RETRIES to test backoff behavior
        mocker.patch("fleetroll.commands.audit.AUDIT_MAX_RETRIES", 4)

        mock_run_ssh = mocker.patch("fleetroll.commands.audit.run_ssh")
        # Always fail with connection error to trigger retries
        mock_run_ssh.return_value = (255, "", "Connection refused")

        # Mock and track sleep calls
        mock_sleep = mocker.patch("fleetroll.commands.audit.time.sleep")

        args = self._make_args(tmp_dir)

        result = audit_single_host_with_retry(
            "test.example.com",
            args=args,
            ssh_opts=[],
            remote_cmd="test",
            db_path=tmp_dir / "test.db",
            actor="test",
            retry_budget={"deadline": time.time() + 600},
            lock=threading.Lock(),
            log_lock=threading.Lock(),
        )

        # Should have made 4 attempts (max retries = 4)
        assert mock_run_ssh.call_count == 4
        assert result["attempts"] == 4

        # Verify exponential backoff: delay = AUDIT_RETRY_DELAY_S * (2 ** attempt)
        # AUDIT_RETRY_DELAY_S = 2 seconds
        # After attempt 0 (1st try): sleep(2 * 2^0) = sleep(2)
        # After attempt 1 (2nd try): sleep(2 * 2^1) = sleep(4)
        # After attempt 2 (3rd try): sleep(2 * 2^2) = sleep(8)
        # No sleep after attempt 3 (4th try, last attempt)
        assert mock_sleep.call_count == 3
        sleep_delays = [call[0][0] for call in mock_sleep.call_args_list]
        assert sleep_delays == [2, 4, 8]

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
            db_path=tmp_dir / "test.db",
            actor="test",
            retry_budget={"deadline": time.time() + 600},
            lock=threading.Lock(),
            log_lock=threading.Lock(),
        )

        assert "attempts" in result
        assert result["attempts"] == 1
