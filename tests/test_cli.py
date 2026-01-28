"""Tests for fleetroll/cli.py - CLI integration tests using Click's CliRunner."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from fleetroll.cli import cli, setup_logging


@pytest.fixture
def runner() -> CliRunner:
    """Create a Click CLI test runner."""
    return CliRunner()


class TestCliHelp:
    """Tests for CLI help output."""

    def test_main_help(self, runner: CliRunner):
        """Main --help shows all commands."""
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "host-audit" in result.output
        assert "debug-host-script" in result.output
        assert "show-vault" in result.output
        assert "host-set-override" in result.output
        assert "host-set-vault" in result.output
        assert "host-unset-override" in result.output

    def test_main_help_shows_description(self, runner: CliRunner):
        """Main --help shows tool description."""
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "FleetRoll" in result.output

    def test_audit_help(self, runner: CliRunner):
        """host-audit --help shows command options."""
        result = runner.invoke(cli, ["host-audit", "--help"])
        assert result.exit_code == 0
        assert "--override-path" in result.output
        assert "--role-path" in result.output
        assert "--vault-path" in result.output
        assert "--workers" in result.output
        assert "--json" in result.output

    def test_set_help(self, runner: CliRunner):
        """host-set-override --help shows command options."""
        result = runner.invoke(cli, ["host-set-override", "--help"])
        assert result.exit_code == 0
        assert "--from-file" in result.output
        assert "--confirm" in result.output
        assert "--mode" in result.output
        assert "--owner" in result.output
        assert "--workers" in result.output

    def test_unset_help(self, runner: CliRunner):
        """host-unset-override --help shows command options."""
        result = runner.invoke(cli, ["host-unset-override", "--help"])
        assert result.exit_code == 0
        assert "--confirm" in result.output
        assert "--no-backup" in result.output
        assert "--reason" in result.output
        assert "--workers" in result.output

    def test_vault_help(self, runner: CliRunner):
        """host-set-vault --help shows command options."""
        result = runner.invoke(cli, ["host-set-vault", "--help"])
        assert result.exit_code == 0
        assert "--from-file" in result.output
        assert "--confirm" in result.output
        assert "--path" in result.output
        assert "--mode" in result.output
        assert "--owner" in result.output
        assert "--workers" in result.output

    def test_debug_host_script_help(self, runner: CliRunner):
        """debug-host-script --help shows command options."""
        result = runner.invoke(cli, ["debug-host-script", "--help"])
        assert result.exit_code == 0
        assert "--override-path" in result.output
        assert "--role-path" in result.output
        assert "--vault-path" in result.output
        assert "--no-content" in result.output
        assert "--wrap" in result.output

    def test_show_vault_help(self, runner: CliRunner):
        """show-vault --help shows command options."""
        result = runner.invoke(cli, ["show-vault", "--help"])
        assert result.exit_code == 0
        assert "--audit-log" in result.output


class TestCliVersion:
    """Tests for CLI version output."""

    def test_version_flag(self, runner: CliRunner):
        """--version shows version information."""
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "fleetroll" in result.output.lower()
        # Should show version number
        assert "0." in result.output or "1." in result.output


class TestCliValidation:
    """Tests for CLI argument validation."""

    def test_set_without_confirm_is_dry_run(self, runner: CliRunner, tmp_dir: Path):
        """host-set-override prints summary and exits without --confirm."""
        test_file = tmp_dir / "override.txt"
        test_file.write_text("test")
        result = runner.invoke(
            cli,
            ["host-set-override", "test.example.com", "--from-file", str(test_file)],
        )
        assert result.exit_code == 0
        assert "DRY RUN" in result.output

    def test_unset_without_confirm_is_dry_run(self, runner: CliRunner):
        """host-unset-override prints summary and exits without --confirm."""
        result = runner.invoke(
            cli,
            ["host-unset-override", "test.example.com"],
        )
        assert result.exit_code == 0
        assert "DRY RUN" in result.output

    def test_audit_requires_host_argument(self, runner: CliRunner):
        """host-audit requires HOST_OR_FILE argument."""
        result = runner.invoke(cli, ["host-audit"])
        assert result.exit_code != 0

    def test_set_requires_host_argument(self, runner: CliRunner, tmp_dir: Path):
        """host-set-override requires HOST argument."""
        test_file = tmp_dir / "override.txt"
        test_file.write_text("test")
        result = runner.invoke(
            cli,
            ["host-set-override", "--from-file", str(test_file), "--confirm"],
        )
        assert result.exit_code != 0

    def test_unset_requires_host_argument(self, runner: CliRunner):
        """host-unset-override requires HOST argument."""
        result = runner.invoke(cli, ["host-unset-override", "--confirm"])
        assert result.exit_code != 0


class TestCliDebugFlag:
    """Tests for --debug flag."""

    def test_debug_flag_accepted(self, runner: CliRunner):
        """--debug flag is accepted at top level."""
        result = runner.invoke(cli, ["--debug", "--help"])
        assert result.exit_code == 0

    def test_debug_short_flag_accepted(self, runner: CliRunner):
        """-d flag is accepted as shorthand for --debug."""
        result = runner.invoke(cli, ["-d", "--help"])
        assert result.exit_code == 0


class TestCliCommonOptions:
    """Tests for common options shared across commands."""

    def test_ssh_option_in_audit(self, runner: CliRunner):
        """--ssh-option is available in host-audit."""
        result = runner.invoke(cli, ["host-audit", "--help"])
        assert "--ssh-option" in result.output

    def test_ssh_option_in_set(self, runner: CliRunner):
        """--ssh-option is available in host-set-override."""
        result = runner.invoke(cli, ["host-set-override", "--help"])
        assert "--ssh-option" in result.output

    def test_ssh_option_in_unset(self, runner: CliRunner):
        """--ssh-option is available in host-unset-override."""
        result = runner.invoke(cli, ["host-unset-override", "--help"])
        assert "--ssh-option" in result.output

    def test_ssh_option_in_vault(self, runner: CliRunner):
        """--ssh-option is available in host-set-vault."""
        result = runner.invoke(cli, ["host-set-vault", "--help"])
        assert "--ssh-option" in result.output

    def test_timeout_options_in_all_commands(self, runner: CliRunner):
        """--timeout and --connect-timeout are available in all commands."""
        for cmd in [
            "host-audit",
            "host-set-override",
            "host-set-vault",
            "host-unset-override",
        ]:
            result = runner.invoke(cli, [cmd, "--help"])
            assert "--timeout" in result.output
            assert "--connect-timeout" in result.output

    def test_json_option_in_all_commands(self, runner: CliRunner):
        """--json option is available in all commands."""
        for cmd in [
            "host-audit",
            "host-set-override",
            "host-set-vault",
            "host-unset-override",
        ]:
            result = runner.invoke(cli, [cmd, "--help"])
            assert "--json" in result.output

    def test_audit_log_option_in_all_commands(self, runner: CliRunner):
        """--audit-log option is available in all commands."""
        for cmd in [
            "host-audit",
            "host-set-override",
            "host-set-vault",
            "host-unset-override",
        ]:
            result = runner.invoke(cli, [cmd, "--help"])
            assert "--audit-log" in result.output


class TestCliLogging:
    """Tests for logging setup behavior."""

    def test_setup_logging_no_duplicate_handlers(self):
        """Repeated setup_logging calls do not add duplicate stderr handlers."""
        logger = logging.getLogger("fleetroll")
        original_handlers = list(logger.handlers)
        try:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)

            setup_logging(debug=False)
            setup_logging(debug=True)

            stderr_handlers = [
                h
                for h in logger.handlers
                if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stderr
            ]
            assert len(stderr_handlers) == 1
        finally:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
            for handler in original_handlers:
                logger.addHandler(handler)
