"""Tests for fleetroll/ssh.py - SSH script generation (security-critical)."""

from __future__ import annotations

from pathlib import Path

from fleetroll.cli import Args
from fleetroll.constants import CONTENT_SENTINEL
from fleetroll.ssh import (
    build_ssh_options,
    remote_audit_script,
    remote_set_script,
    remote_unset_script,
)


class TestBuildSshOptions:
    """Tests for build_ssh_options function."""

    def test_default_options(self, tmp_audit_log: Path):
        """Default options include ConnectTimeout and StrictHostKeyChecking."""
        args = Args(
            ssh_option=None,
            connect_timeout=10,
            audit_log=str(tmp_audit_log),
        )
        opts = build_ssh_options(args)
        assert "-o" in opts
        assert "ConnectTimeout=10" in opts
        # Note: BatchMode=yes is added in run_ssh, not build_ssh_options
        assert "StrictHostKeyChecking=accept-new" in opts

    def test_custom_connect_timeout(self, tmp_audit_log: Path):
        """Custom connect timeout is used."""
        args = Args(
            ssh_option=None,
            connect_timeout=30,
            audit_log=str(tmp_audit_log),
        )
        opts = build_ssh_options(args)
        assert "ConnectTimeout=30" in opts

    def test_single_ssh_option(self, tmp_audit_log: Path):
        """Single --ssh-option is parsed correctly."""
        args = Args(
            ssh_option=["-p 2222"],
            connect_timeout=10,
            audit_log=str(tmp_audit_log),
        )
        opts = build_ssh_options(args)
        assert "-p" in opts
        assert "2222" in opts

    def test_multiple_ssh_options(self, tmp_audit_log: Path):
        """Multiple --ssh-option flags are parsed correctly."""
        args = Args(
            ssh_option=["-p 2222", "-J bastion.example.com"],
            connect_timeout=10,
            audit_log=str(tmp_audit_log),
        )
        opts = build_ssh_options(args)
        assert "-p" in opts
        assert "2222" in opts
        assert "-J" in opts
        assert "bastion.example.com" in opts

    def test_complex_option_with_equals(self, tmp_audit_log: Path):
        """Options with = are handled correctly."""
        args = Args(
            ssh_option=["-o UserKnownHostsFile=/dev/null"],
            connect_timeout=10,
            audit_log=str(tmp_audit_log),
        )
        opts = build_ssh_options(args)
        assert "UserKnownHostsFile=/dev/null" in opts


class TestRemoteAuditScript:
    """Tests for remote_audit_script function."""

    def test_basic_generation(self):
        """Script is generated with sh -c wrapper."""
        script = remote_audit_script(
            "/etc/puppet/ronin_settings",
            "/etc/puppet_role",
            "/root/vault.yaml",
            include_content=True,
        )
        assert script.startswith("sh -c ")
        assert "/etc/puppet/ronin_settings" in script
        assert "/etc/puppet_role" in script

    def test_include_content_true(self):
        """When include_content=True, script includes content output."""
        script = remote_audit_script(
            "/etc/puppet/ronin_settings",
            "/etc/puppet_role",
            "/root/vault.yaml",
            include_content=True,
        )
        assert CONTENT_SENTINEL in script
        # The include_content_cmd variable should be "true"
        assert "if true" in script

    def test_include_content_false(self):
        """When include_content=False, content output is skipped."""
        script = remote_audit_script(
            "/etc/puppet/ronin_settings",
            "/etc/puppet_role",
            "/root/vault.yaml",
            include_content=False,
        )
        # The include_content_cmd variable should be "false"
        assert "if false" in script

    def test_outputs_role_present(self):
        """Script outputs ROLE_PRESENT marker."""
        script = remote_audit_script(
            "/etc/test",
            "/etc/role",
            "/root/vault.yaml",
            include_content=True,
        )
        assert "ROLE_PRESENT=" in script

    def test_outputs_override_present(self):
        """Script outputs OVERRIDE_PRESENT marker."""
        script = remote_audit_script(
            "/etc/test",
            "/etc/role",
            "/root/vault.yaml",
            include_content=True,
        )
        assert "OVERRIDE_PRESENT=" in script

    def test_outputs_override_metadata(self):
        """Script outputs override metadata (mode, owner, etc)."""
        script = remote_audit_script(
            "/etc/test",
            "/etc/role",
            "/root/vault.yaml",
            include_content=True,
        )
        assert "OVERRIDE_MODE=" in script
        assert "OVERRIDE_OWNER=" in script
        assert "OVERRIDE_GROUP=" in script
        assert "OVERRIDE_SIZE=" in script
        assert "OVERRIDE_MTIME=" in script

    def test_path_with_spaces_quoted(self):
        """Paths with spaces are properly quoted."""
        script = remote_audit_script(
            "/path/with spaces/file",
            "/another path/role",
            "/root/vault.yaml",
            include_content=True,
        )
        # Script should still be valid (starts with sh -c)
        assert script.startswith("sh -c ")
        # Paths should be quoted in the script
        assert "with spaces" in script

    def test_uses_sudo(self):
        """Script uses sudo -n for privileged operations."""
        script = remote_audit_script(
            "/etc/test",
            "/etc/role",
            "/root/vault.yaml",
            include_content=True,
        )
        assert "sudo -n" in script


class TestRemoteSetScript:
    """Tests for remote_set_script function."""

    def test_basic_generation(self):
        """Script is generated with sh -c wrapper."""
        script = remote_set_script(
            "/etc/puppet/ronin_settings",
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
            "/etc/test",
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
            "/etc/test",
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
            "/etc/test",
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
            "/etc/test",
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
            "/etc/test",
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
            "/etc/test",
            mode="0644",
            owner="root",
            group="root",
            backup=False,
            backup_suffix="suffix",
        )
        assert "tee" in script


class TestRemoteUnsetScript:
    """Tests for remote_unset_script function."""

    def test_basic_generation(self):
        """Script is generated with sh -c wrapper."""
        script = remote_unset_script(
            "/etc/puppet/ronin_settings",
            backup=True,
            backup_suffix="20240101T000000Z",
        )
        assert script.startswith("sh -c ")
        assert "/etc/puppet/ronin_settings" in script

    def test_uses_rm(self):
        """Script uses rm to remove file."""
        script = remote_unset_script(
            "/etc/test",
            backup=False,
            backup_suffix="suffix",
        )
        assert "rm -f" in script

    def test_backup_true(self):
        """When backup=True, existing file is backed up before removal."""
        script = remote_unset_script(
            "/etc/test",
            backup=True,
            backup_suffix="20240101T000000Z",
        )
        assert "cp -a" in script
        assert ".bak." in script

    def test_backup_false(self):
        """When backup=False, file is removed without backup."""
        script = remote_unset_script(
            "/etc/test",
            backup=False,
            backup_suffix="suffix",
        )
        assert "if false" in script

    def test_outputs_removed_status(self):
        """Script outputs REMOVED=1 or REMOVED=0."""
        script = remote_unset_script(
            "/etc/test",
            backup=False,
            backup_suffix="suffix",
        )
        assert "REMOVED=1" in script
        assert "REMOVED=0" in script

    def test_handles_nonexistent_file(self):
        """Script handles case where file doesn't exist."""
        script = remote_unset_script(
            "/etc/test",
            backup=False,
            backup_suffix="suffix",
        )
        # Script should test for file existence
        assert "test -e" in script
