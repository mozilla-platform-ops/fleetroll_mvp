"""Tests for fleetroll/ssh.py - SSH execution with mocked subprocess."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest
from fleetroll.constants import SSH_TIMEOUT_EXIT_CODE
from fleetroll.exceptions import FleetRollError
from fleetroll.ssh import run_ssh


class TestRunSsh:
    """Tests for run_ssh function with mocked subprocess."""

    def test_successful_execution(self, mocker):
        """Returns (0, stdout, stderr) on success."""
        mock_run = mocker.patch("fleetroll.ssh.subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=b"output",
            stderr=b"",
        )

        rc, out, err = run_ssh(
            "test.example.com",
            "echo hello",
            ssh_options=["-o", "StrictHostKeyChecking=no"],
            timeout_s=60,
        )

        assert rc == 0
        assert out == "output"
        assert err == ""

    def test_command_construction(self, mocker):
        """SSH command is constructed correctly."""
        mock_run = mocker.patch("fleetroll.ssh.subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=b"",
            stderr=b"",
        )

        run_ssh(
            "user@host.example.com",
            "test command",
            ssh_options=["-p", "2222"],
            timeout_s=30,
        )

        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert cmd[0] == "ssh"
        assert "-o" in cmd
        assert "BatchMode=yes" in cmd
        assert "-p" in cmd
        assert "2222" in cmd
        assert "user@host.example.com" in cmd
        assert "test command" in cmd

    def test_force_tty_allocates_pty_with_explicit_winsize(self, mocker):
        """force_tty=True opens a local pty and sets TIOCSWINSZ to known dims."""
        import struct
        import termios

        from fleetroll.constants import SSH_PTY_COLS, SSH_PTY_ROWS

        mock_run = mocker.patch("fleetroll.ssh.subprocess.run")
        mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
        mock_openpty = mocker.patch("fleetroll.ssh.pty.openpty", return_value=(10, 11))
        mock_ioctl = mocker.patch("fleetroll.ssh.fcntl.ioctl")
        mocker.patch("fleetroll.ssh.os.close")

        run_ssh(
            "test.example.com",
            "echo hi",
            ssh_options=[],
            timeout_s=60,
            force_tty=True,
        )

        mock_openpty.assert_called_once()
        # ioctl called on the slave fd with TIOCSWINSZ and the right winsize
        args, _ = mock_ioctl.call_args
        assert args[0] == 11, "ioctl must target the pty slave fd"
        assert args[1] == termios.TIOCSWINSZ
        assert args[2] == struct.pack("HHHH", SSH_PTY_ROWS, SSH_PTY_COLS, 0, 0)
        # subprocess.run got the slave fd as stdin
        assert mock_run.call_args.kwargs["stdin"] == 11
        # And -t is on the ssh command line
        assert "-t" in mock_run.call_args[0][0]

    def test_no_force_tty_skips_pty_allocation(self, mocker):
        """force_tty=False does not open a pty or set stdin."""
        mock_run = mocker.patch("fleetroll.ssh.subprocess.run")
        mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
        mock_openpty = mocker.patch("fleetroll.ssh.pty.openpty")

        run_ssh("test.example.com", "echo hi", ssh_options=[], timeout_s=60)

        mock_openpty.assert_not_called()
        assert mock_run.call_args.kwargs.get("stdin") is None

    def test_force_tty_rejects_input_bytes(self, mocker):
        """force_tty=True with input_bytes raises (mutually exclusive)."""
        mocker.patch("fleetroll.ssh.subprocess.run")
        with pytest.raises(FleetRollError, match=r"force_tty.*input_bytes"):
            run_ssh(
                "test.example.com",
                "cat",
                ssh_options=[],
                timeout_s=60,
                force_tty=True,
                input_bytes=b"data",
            )

    def test_timeout_passed_to_subprocess(self, mocker):
        """Timeout is passed to subprocess.run."""
        mock_run = mocker.patch("fleetroll.ssh.subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=b"",
            stderr=b"",
        )

        run_ssh(
            "test.example.com",
            "echo hello",
            ssh_options=[],
            timeout_s=45,
        )

        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["timeout"] == 45

    def test_failure_returns_nonzero(self, mocker):
        """Non-zero return code is passed through."""
        mock_run = mocker.patch("fleetroll.ssh.subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=255,
            stdout=b"",
            stderr=b"Connection refused",
        )

        rc, out, err = run_ssh(
            "test.example.com",
            "echo hello",
            ssh_options=[],
            timeout_s=60,
        )

        assert rc == 255
        assert err == "Connection refused"

    def test_timeout_returns_special_code(self, mocker):
        """Timeout returns SSH_TIMEOUT_EXIT_CODE with partial output."""
        mock_run = mocker.patch("fleetroll.ssh.subprocess.run")
        exc = subprocess.TimeoutExpired(cmd=["ssh"], timeout=60)
        exc.stdout = b"partial output"
        exc.stderr = b"timeout error"
        mock_run.side_effect = exc

        rc, out, err = run_ssh(
            "test.example.com",
            "sleep 100",
            ssh_options=[],
            timeout_s=60,
        )

        assert rc == SSH_TIMEOUT_EXIT_CODE
        assert out == "partial output"

    def test_timeout_handles_none_output(self, mocker):
        """Timeout handles None stdout/stderr gracefully."""
        mock_run = mocker.patch("fleetroll.ssh.subprocess.run")
        exc = subprocess.TimeoutExpired(cmd=["ssh"], timeout=60)
        exc.stdout = None
        exc.stderr = None
        mock_run.side_effect = exc

        rc, out, err = run_ssh(
            "test.example.com",
            "sleep 100",
            ssh_options=[],
            timeout_s=60,
        )

        assert rc == SSH_TIMEOUT_EXIT_CODE
        assert out == ""
        assert "timeout" in err.lower()

    def test_missing_ssh_binary_raises(self, mocker):
        """Raises FleetRollError when ssh binary not found."""
        mock_run = mocker.patch("fleetroll.ssh.subprocess.run")
        mock_run.side_effect = FileNotFoundError()

        with pytest.raises(FleetRollError, match="ssh binary not found"):
            run_ssh(
                "test.example.com",
                "echo hello",
                ssh_options=[],
                timeout_s=60,
            )

    def test_input_bytes_passed(self, mocker):
        """Input bytes are passed to subprocess stdin."""
        mock_run = mocker.patch("fleetroll.ssh.subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=b"",
            stderr=b"",
        )

        run_ssh(
            "test.example.com",
            "cat",
            ssh_options=[],
            input_bytes=b"stdin data",
            timeout_s=60,
        )

        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["input"] == b"stdin data"

    def test_decodes_utf8_output(self, mocker):
        """Decodes UTF-8 output correctly."""
        mock_run = mocker.patch("fleetroll.ssh.subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="héllo wörld".encode(),
            stderr=b"",
        )

        rc, out, err = run_ssh(
            "test.example.com",
            "echo test",
            ssh_options=[],
            timeout_s=60,
        )

        assert out == "héllo wörld"

    def test_handles_invalid_utf8(self, mocker):
        """Handles invalid UTF-8 with replacement characters."""
        mock_run = mocker.patch("fleetroll.ssh.subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=b"\xff\xfe invalid",
            stderr=b"",
        )

        rc, out, err = run_ssh(
            "test.example.com",
            "echo test",
            ssh_options=[],
            timeout_s=60,
        )

        # Should not raise, uses "replace" error handler
        assert "invalid" in out

    def test_check_false(self, mocker):
        """subprocess.run is called with check=False."""
        mock_run = mocker.patch("fleetroll.ssh.subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout=b"",
            stderr=b"",
        )

        run_ssh(
            "test.example.com",
            "false",
            ssh_options=[],
            timeout_s=60,
        )

        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["check"] is False
