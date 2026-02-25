"""Tests for fleetroll/cli.py - CLI integration tests using Click's CliRunner."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from fleetroll.cli import cli, setup_logging
from fleetroll.constants import AUDIT_FILE_NAME, DB_FILE_NAME


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
        # Path options removed - OS detection automatic
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
        # --path removed - OS detection automatic
        assert "--mode" in result.output
        assert "--owner" in result.output
        assert "--workers" in result.output

    def test_debug_host_script_help(self, runner: CliRunner):
        """debug-host-script --help shows command options."""
        result = runner.invoke(cli, ["debug-host-script", "--help"])
        assert result.exit_code == 0
        # Path options removed - OS detection automatic
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


class TestCliMissingHelp:
    """--help smoke tests for commands not yet covered."""

    def test_maintain_help(self, runner: CliRunner):
        """maintain --help shows command options."""
        result = runner.invoke(cli, ["maintain", "--help"])
        assert result.exit_code == 0
        assert "--confirm" in result.output
        assert "--force" in result.output

    def test_tc_fetch_help(self, runner: CliRunner):
        """tc-fetch --help shows command options."""
        result = runner.invoke(cli, ["tc-fetch", "--help"])
        assert result.exit_code == 0
        assert "--verbose" in result.output
        assert "--quiet" in result.output

    def test_gh_fetch_help(self, runner: CliRunner):
        """gh-fetch --help shows command options."""
        result = runner.invoke(cli, ["gh-fetch", "--help"])
        assert result.exit_code == 0
        assert "--quiet" in result.output

    def test_host_monitor_help(self, runner: CliRunner):
        """host-monitor --help shows command options."""
        result = runner.invoke(cli, ["host-monitor", "--help"])
        assert result.exit_code == 0
        assert "--once" in result.output
        assert "--sort" in result.output

    def test_show_override_help(self, runner: CliRunner):
        """show-override --help shows command options."""
        result = runner.invoke(cli, ["show-override", "--help"])
        assert result.exit_code == 0
        assert "--audit-log" in result.output


class TestCliMutualExclusion:
    """Tests for mutually exclusive flags."""

    def test_host_audit_verbose_and_quiet_rejected(self, runner: CliRunner):
        """host-audit rejects --verbose and --quiet together."""
        result = runner.invoke(cli, ["host-audit", "somehost", "--verbose", "--quiet"])
        assert result.exit_code != 0

    def test_tc_fetch_verbose_and_quiet_rejected(self, runner: CliRunner):
        """tc-fetch rejects --verbose and --quiet together."""
        result = runner.invoke(cli, ["tc-fetch", "somehost", "--verbose", "--quiet"])
        assert result.exit_code != 0


class TestMaintainCommand:
    """Tests for the maintain command via CliRunner."""

    def _audit_log_arg(self, tmp_path: Path) -> str:
        """Return the --audit-log value pointing into tmp_path."""
        return str(tmp_path / AUDIT_FILE_NAME)

    def test_dry_run_no_files(self, runner: CliRunner, tmp_path: Path):
        """maintain without --confirm prints dry-run header; skips missing files."""
        result = runner.invoke(cli, ["maintain", "--audit-log", self._audit_log_arg(tmp_path)])
        assert result.exit_code == 0
        assert "DRY RUN" in result.output
        assert "Run again with --confirm" in result.output
        # Both files are absent
        assert f"SKIP {AUDIT_FILE_NAME}" in result.output
        assert f"SKIP {DB_FILE_NAME}" in result.output

    def test_dry_run_small_file_skipped(self, runner: CliRunner, tmp_path: Path):
        """maintain skips files below the 100 MB threshold without --force."""
        audit = tmp_path / AUDIT_FILE_NAME
        audit.write_text("small content")
        result = runner.invoke(cli, ["maintain", "--audit-log", self._audit_log_arg(tmp_path)])
        assert result.exit_code == 0
        assert "below" in result.output
        assert "threshold" in result.output

    def test_dry_run_force_would_rotate(self, runner: CliRunner, tmp_path: Path):
        """maintain --force reports it would rotate the audit log (dry-run)."""
        audit = tmp_path / AUDIT_FILE_NAME
        audit.write_text("content")
        result = runner.invoke(
            cli, ["maintain", "--audit-log", self._audit_log_arg(tmp_path), "--force"]
        )
        assert result.exit_code == 0
        assert "DRY RUN" in result.output
        assert "Would rotate" in result.output
        # File should still exist — dry run does not modify it
        assert audit.exists()

    def test_confirm_force_rotates_audit_log(self, runner: CliRunner, tmp_path: Path):
        """maintain --confirm --force actually renames the audit log."""
        audit = tmp_path / AUDIT_FILE_NAME
        audit.write_text("content to rotate")
        result = runner.invoke(
            cli,
            ["maintain", "--audit-log", self._audit_log_arg(tmp_path), "--confirm", "--force"],
        )
        assert result.exit_code == 0
        assert "OK" in result.output
        # Original file is gone; an archive was created
        assert not audit.exists()
        archives = list(tmp_path.glob(f"{AUDIT_FILE_NAME}.*"))
        assert len(archives) == 1

    def test_confirm_compacts_database(self, runner: CliRunner, tmp_path: Path):
        """maintain --confirm compacts the SQLite database when it exists."""
        from fleetroll.db import init_db

        db_path = tmp_path / DB_FILE_NAME
        init_db(db_path)
        result = runner.invoke(
            cli,
            ["maintain", "--audit-log", self._audit_log_arg(tmp_path), "--confirm", "--force"],
        )
        assert result.exit_code == 0
        assert f"OK {DB_FILE_NAME}" in result.output
        assert "compacted" in result.output

    def test_confirm_no_files_rotated_summary(self, runner: CliRunner, tmp_path: Path):
        """maintain --confirm with no files to rotate shows appropriate summary."""
        from fleetroll.db import init_db

        db_path = tmp_path / DB_FILE_NAME
        init_db(db_path)
        result = runner.invoke(
            cli, ["maintain", "--audit-log", self._audit_log_arg(tmp_path), "--confirm"]
        )
        assert result.exit_code == 0
        assert "No files rotated" in result.output

    def test_confirm_rotated_summary(self, runner: CliRunner, tmp_path: Path):
        """maintain --confirm with a rotated file shows rotated count in summary."""
        audit = tmp_path / AUDIT_FILE_NAME
        audit.write_text("content")
        result = runner.invoke(
            cli,
            ["maintain", "--audit-log", self._audit_log_arg(tmp_path), "--confirm", "--force"],
        )
        assert result.exit_code == 0
        assert "1 file(s) rotated" in result.output
