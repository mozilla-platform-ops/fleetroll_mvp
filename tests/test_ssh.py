"""Tests for fleetroll/ssh.py - SSH script generation (security-critical)."""

from __future__ import annotations

from pathlib import Path

from fleetroll.cli_types import HostAuditArgs
from fleetroll.constants import CONTENT_SENTINEL
from fleetroll.ssh import (
    build_ssh_options,
    remote_audit_script,
    remote_set_script,
    remote_unset_script,
)


def make_test_args(
    *, ssh_option: list[str] | None = None, connect_timeout: int = 10
) -> HostAuditArgs:
    """Helper to create HostAuditArgs for testing with minimal required fields."""
    return HostAuditArgs(
        host="test.example.com",
        ssh_option=ssh_option,
        connect_timeout=connect_timeout,
        timeout=60,
        audit_log=None,
        json=False,
        no_content=False,
        workers=10,
        batch_timeout=600,
        verbose=False,
        quiet=False,
    )


class TestBuildSshOptions:
    """Tests for build_ssh_options function."""

    def test_default_options(self, tmp_audit_log: Path):
        """Default options include ConnectTimeout and StrictHostKeyChecking."""
        args = make_test_args()
        opts = build_ssh_options(args)
        assert "-o" in opts
        assert "ConnectTimeout=10" in opts
        # Note: BatchMode=yes is added in run_ssh, not build_ssh_options
        assert "StrictHostKeyChecking=accept-new" in opts

    def test_custom_connect_timeout(self, tmp_audit_log: Path):
        """Custom connect timeout is used."""
        args = make_test_args(connect_timeout=30)
        opts = build_ssh_options(args)
        assert "ConnectTimeout=30" in opts

    def test_single_ssh_option(self, tmp_audit_log: Path):
        """Single --ssh-option is parsed correctly."""
        args = make_test_args(ssh_option=["-p 2222"])
        opts = build_ssh_options(args)
        assert "-p" in opts
        assert "2222" in opts

    def test_multiple_ssh_options(self, tmp_audit_log: Path):
        """Multiple --ssh-option flags are parsed correctly."""
        args = make_test_args(ssh_option=["-p 2222", "-J bastion.example.com"])
        opts = build_ssh_options(args)
        assert "-p" in opts
        assert "2222" in opts
        assert "-J" in opts
        assert "bastion.example.com" in opts

    def test_complex_option_with_equals(self, tmp_audit_log: Path):
        """Options with = are handled correctly."""
        args = make_test_args(ssh_option=["-o UserKnownHostsFile=/dev/null"])
        opts = build_ssh_options(args)
        assert "UserKnownHostsFile=/dev/null" in opts


class TestRemoteAuditScript:
    """Tests for remote_audit_script function."""

    def test_basic_generation(self):
        """Script is generated with sh -c wrapper."""
        script = remote_audit_script(include_content=True)
        assert script.startswith("sh -c ")
        assert "/etc/puppet/ronin_settings" in script
        assert "/etc/puppet_role" in script

    def test_include_content_true(self):
        """When include_content=True, script includes content output."""
        script = remote_audit_script(include_content=True)
        assert CONTENT_SENTINEL in script
        # The include_content_cmd variable should be "true"
        assert "if true" in script

    def test_include_content_false(self):
        """When include_content=False, content output is skipped."""
        script = remote_audit_script(include_content=False)
        # The include_content_cmd variable should be "false"
        assert "if false" in script

    def test_outputs_role_present(self):
        """Script outputs ROLE_PRESENT marker."""
        script = remote_audit_script(include_content=True)
        assert "ROLE_PRESENT=" in script

    def test_outputs_override_present(self):
        """Script outputs OVERRIDE_PRESENT marker."""
        script = remote_audit_script(include_content=True)
        assert "OVERRIDE_PRESENT=" in script

    def test_outputs_override_metadata(self):
        """Script outputs override metadata (mode, owner, etc)."""
        script = remote_audit_script(include_content=True)
        assert "OVERRIDE_MODE=" in script
        assert "OVERRIDE_OWNER=" in script
        assert "OVERRIDE_GROUP=" in script
        assert "OVERRIDE_SIZE=" in script
        assert "OVERRIDE_MTIME=" in script

    def test_paths_hardcoded_in_script(self):
        """Paths are hardcoded in script with OS detection."""
        script = remote_audit_script(include_content=True)
        # Script should contain both Linux and Darwin paths
        assert "/etc/puppet/ronin_settings" in script
        assert "/opt/puppet_environments/ronin_settings" in script
        assert "uname -s" in script  # OS detection
        assert script.startswith("sh -c ")

    def test_uses_sudo(self):
        """Script uses sudo -n for privileged operations."""
        script = remote_audit_script(include_content=True)
        assert "sudo -n" in script


class TestRemoteSetScript:
    """Tests for remote_set_script function."""

    def test_basic_generation(self):
        """Script is generated with sh -c wrapper."""
        script = remote_set_script(
            mode="0644",
            owner="root",
            group="root",
            backup=True,
            backup_suffix="20240101T000000Z",
        )
        assert script.startswith("sh -c ")
        assert "/etc/puppet/ronin_settings" in script

    def test_uses_atomic_write(self):
        """Script uses mktemp and mv for atomic writes."""
        script = remote_set_script(
            mode="0644",
            owner="root",
            group="root",
            backup=False,
            backup_suffix="suffix",
        )
        assert "mktemp" in script
        assert "mv" in script

    def test_backup_true(self):
        """When backup=True, existing file is backed up."""
        script = remote_set_script(
            mode="0644",
            owner="root",
            group="root",
            backup=True,
            backup_suffix="20240101T000000Z",
        )
        assert "cp -a" in script
        assert ".bak." in script
        assert "20240101T000000Z" in script

    def test_backup_false(self):
        """When backup=False, backup is skipped."""
        script = remote_set_script(
            mode="0644",
            owner="root",
            group="root",
            backup=False,
            backup_suffix="suffix",
        )
        # The backup condition should be "if false"
        assert "if false" in script

    def test_mode_applied(self):
        """Mode is applied via chmod."""
        script = remote_set_script(
            mode="0755",
            owner="root",
            group="root",
            backup=False,
            backup_suffix="suffix",
        )
        assert "chmod" in script
        assert "0755" in script

    def test_owner_group_applied(self):
        """Owner and group are applied via chown."""
        script = remote_set_script(
            mode="0644",
            owner="nobody",
            group="nogroup",
            backup=False,
            backup_suffix="suffix",
        )
        assert "chown" in script
        assert "nobody:nogroup" in script

    def test_uses_tee_for_stdin(self):
        """Script uses tee to write stdin to temp file."""
        script = remote_set_script(
            mode="0644",
            owner="root",
            group="root",
            backup=False,
            backup_suffix="suffix",
        )
        assert "tee" in script

    def test_includes_exit_trap_cleanup(self):
        """Script includes EXIT trap for temp file cleanup."""
        script = remote_set_script(
            mode="0644",
            owner="root",
            group="root",
            backup=False,
            backup_suffix="suffix",
        )
        assert "trap" in script
        assert "EXIT" in script
        assert "rm -f" in script

    def test_trap_before_mktemp(self):
        """EXIT trap is set before mktemp is called."""
        script = remote_set_script(
            mode="0644",
            owner="root",
            group="root",
            backup=False,
            backup_suffix="suffix",
        )
        trap_pos = script.find("trap")
        mktemp_pos = script.find("mktemp")
        assert trap_pos > 0, "trap not found"
        assert mktemp_pos > 0, "mktemp not found"
        assert trap_pos < mktemp_pos, "trap must be set before mktemp"


class TestRemoteUnsetScript:
    """Tests for remote_unset_script function."""

    def test_basic_generation(self):
        """Script is generated with sh -c wrapper."""
        script = remote_unset_script(
            backup=True,
            backup_suffix="20240101T000000Z",
        )
        assert script.startswith("sh -c ")
        assert "/etc/puppet/ronin_settings" in script

    def test_uses_rm(self):
        """Script uses rm to remove file."""
        script = remote_unset_script(
            backup=False,
            backup_suffix="suffix",
        )
        assert "rm -f" in script

    def test_backup_true(self):
        """When backup=True, existing file is backed up before removal."""
        script = remote_unset_script(
            backup=True,
            backup_suffix="20240101T000000Z",
        )
        assert "cp -a" in script
        assert ".bak." in script

    def test_backup_false(self):
        """When backup=False, file is removed without backup."""
        script = remote_unset_script(
            backup=False,
            backup_suffix="suffix",
        )
        assert "if false" in script

    def test_outputs_removed_status(self):
        """Script outputs REMOVED=1 or REMOVED=0."""
        script = remote_unset_script(
            backup=False,
            backup_suffix="suffix",
        )
        assert "REMOVED=1" in script
        assert "REMOVED=0" in script

    def test_handles_nonexistent_file(self):
        """Script handles case where file doesn't exist."""
        script = remote_unset_script(
            backup=False,
            backup_suffix="suffix",
        )
        # Script should test for file existence
        assert "test -e" in script

    def test_includes_exit_trap_defensive(self):
        """Script includes EXIT trap for defensive cleanup."""
        script = remote_unset_script(
            backup=True,
            backup_suffix="20240101T000000Z",
        )
        assert "trap" in script
        assert "EXIT" in script


class TestOsXScriptGeneration:
    """Tests for OS X (Darwin) support in generated audit scripts."""

    def test_script_contains_os_detection(self):
        """Generated script includes OS detection."""
        script = remote_audit_script(include_content=True)
        assert "uname -s" in script
        assert "os_type=" in script

    def test_darwin_paths_hardcoded(self):
        """Script includes Darwin-specific paths."""
        script = remote_audit_script(include_content=True)
        assert "/opt/puppet_environments/ronin_settings" in script
        assert "/var/root/vault.yaml" in script

    def test_linux_paths_hardcoded(self):
        """Script includes Linux-specific paths."""
        script = remote_audit_script(include_content=True)
        assert "/etc/puppet/ronin_settings" in script
        assert "/root/vault.yaml" in script

    def test_role_path_same_on_both(self):
        """Script uses same role path for both OS."""
        script = remote_audit_script(include_content=True)
        # Role path should appear only once (same for both)
        assert "/etc/puppet_role" in script

    def test_bsd_stat_commands(self):
        """Script includes BSD stat format for Darwin."""
        script = remote_audit_script(include_content=True)
        assert "stat -f" in script

    def test_gnu_stat_commands(self):
        """Script includes GNU stat format for Linux."""
        script = remote_audit_script(include_content=True)
        assert "stat -c" in script

    def test_darwin_uptime_logic(self):
        """Script includes Darwin uptime via sysctl."""
        script = remote_audit_script(include_content=True)
        assert "kern.boottime" in script
        assert "sysctl" in script

    def test_linux_uptime_logic(self):
        """Script includes Linux uptime via /proc/uptime."""
        script = remote_audit_script(include_content=True)
        assert "/proc/uptime" in script

    def test_puppet_state_collection_supports_both_platforms(self):
        """Script collects puppet state on both platforms via JSON metadata."""
        script = remote_audit_script(include_content=True)
        # New JSON metadata file approach works on both Darwin and Linux
        assert "/etc/puppet/last_run_metadata.json" in script
        assert "PP_STATE_JSON" in script
        # Fall back to YAML parsing (Linux-only) when JSON not available
        assert "last_run_report.yaml" in script
        assert "last_run_summary.yaml" in script

    def test_set_script_has_os_detection(self):
        """remote_set_script includes OS detection."""
        script = remote_set_script(
            mode="0644",
            owner="root",
            group="root",
            backup=True,
            backup_suffix="20240101T000000Z",
        )
        assert "uname -s" in script
        assert "Darwin" in script

    def test_unset_script_has_os_detection(self):
        """remote_unset_script includes OS detection."""
        script = remote_unset_script(
            backup=True,
            backup_suffix="20240101T000000Z",
        )
        assert "uname -s" in script
        assert "Darwin" in script
