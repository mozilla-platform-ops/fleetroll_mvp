"""Tests for fleetroll/commands/vault.py - set vault command."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fleetroll.cli_types import HostSetVaultArgs
from fleetroll.commands.vault import cmd_host_set_vault
from fleetroll.exceptions import UserError


class TestCmdHostSetVault:
    """Tests for cmd_host_set_vault function."""

    def test_dry_run_without_confirm(
        self, mocker, mock_args_vault: HostSetVaultArgs, tmp_dir: Path
    ):
        """Prints summary and exits when --confirm not provided."""
        content_file = tmp_dir / "vault.yaml"
        content_file.write_text("vault: data\n")
        mock_args_vault.from_file = str(content_file)
        mock_args_vault.confirm = False
        mock_args_vault.json = False
        mock_run_ssh = mocker.patch("fleetroll.commands.vault.run_ssh")
        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0]))

        cmd_host_set_vault(mock_args_vault)

        mock_run_ssh.assert_not_called()
        assert any("DRY RUN" in line for line in captured)

    def test_requires_from_file(self, mock_args_vault: HostSetVaultArgs, tmp_dir: Path):
        """Raises UserError when --from-file is missing."""
        mock_args_vault.from_file = None
        mock_args_vault.audit_log = str(tmp_dir / "audit.jsonl")
        with pytest.raises(UserError, match="--from-file"):
            cmd_host_set_vault(mock_args_vault)

    def test_successful_set_with_from_file(
        self, mocker, mock_args_vault: HostSetVaultArgs, tmp_dir: Path
    ):
        """Successfully sets vault with --from-file."""
        content_file = tmp_dir / "vault.yaml"
        content_file.write_text("vault: data\n")

        mock_args_vault.from_file = str(content_file)
        mock_args_vault.audit_log = str(tmp_dir / "audit.jsonl")
        mock_args_vault.json = True

        mock_run_ssh = mocker.patch("fleetroll.commands.vault.run_ssh")
        mock_run_ssh.return_value = (0, "", "")

        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0]))

        cmd_host_set_vault(mock_args_vault)

        mock_run_ssh.assert_called_once()
        output = json.loads(captured[0])
        assert output["ok"] is True
        assert output["action"] == "host.set_vault"
        assert output["host"] == "test.example.com"

    def test_writes_to_audit_log(self, mocker, mock_args_vault: HostSetVaultArgs, tmp_dir: Path):
        """Writes result to audit log file."""
        content_file = tmp_dir / "vault.yaml"
        content_file.write_text("vault: data\n")
        audit_log = tmp_dir / "audit.jsonl"
        mock_args_vault.from_file = str(content_file)
        mock_args_vault.audit_log = str(audit_log)
        mock_args_vault.json = True

        mock_run_ssh = mocker.patch("fleetroll.commands.vault.run_ssh")
        mock_run_ssh.return_value = (0, "", "")

        mocker.patch("builtins.print")

        cmd_host_set_vault(mock_args_vault)

        assert audit_log.exists()
        record = json.loads(audit_log.read_text().strip())
        assert record["action"] == "host.set_vault"

    def test_stores_local_copy(self, mocker, mock_args_vault: HostSetVaultArgs, tmp_dir: Path):
        """Stores a local copy in the vault_yamls directory."""
        content = "vault: data\n"
        content_file = tmp_dir / "vault.yaml"
        content_file.write_text(content)
        audit_log = tmp_dir / ".fleetroll" / "audit.jsonl"
        mock_args_vault.from_file = str(content_file)
        mock_args_vault.audit_log = str(audit_log)
        mock_args_vault.json = True

        mock_run_ssh = mocker.patch("fleetroll.commands.vault.run_ssh")
        mock_run_ssh.return_value = (0, "", "")

        mocker.patch("builtins.print")

        cmd_host_set_vault(mock_args_vault)

        sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        expected = audit_log.parent / "vault_yamls" / sha[:12]
        assert expected.exists()

    def test_dry_run_batch_shows_host_preview_small_list(
        self, mocker, mock_args_vault: HostSetVaultArgs, tmp_dir: Path
    ):
        """Dry-run batch mode shows all hosts when count <= 5."""
        content_file = tmp_dir / "vault.yaml"
        content_file.write_text("vault: data\n")
        mock_args_vault.from_file = str(content_file)
        mock_args_vault.confirm = False
        mock_args_vault.json = False

        hosts_file = tmp_dir / "hosts.txt"
        hosts_file.write_text("host1.example.com\nhost2.example.com\nhost3.example.com\n")
        mock_args_vault.host = str(hosts_file)

        mock_run_ssh = mocker.patch("fleetroll.commands.vault.run_ssh")
        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(str(a[0])))

        cmd_host_set_vault(mock_args_vault)

        mock_run_ssh.assert_not_called()
        output = "\n".join(captured)
        assert "Hosts file:" in output
        assert " 3" in output
        assert "  - host1.example.com" in output
        assert "  - host2.example.com" in output
        assert "  - host3.example.com" in output
        assert "more host" not in output

    def test_dry_run_batch_shows_host_preview_large_list(
        self, mocker, mock_args_vault: HostSetVaultArgs, tmp_dir: Path
    ):
        """Dry-run batch mode shows first 5 hosts + overflow message when count > 5."""
        content_file = tmp_dir / "vault.yaml"
        content_file.write_text("vault: data\n")
        mock_args_vault.from_file = str(content_file)
        mock_args_vault.confirm = False
        mock_args_vault.json = False

        hosts_file = tmp_dir / "hosts.txt"
        hosts = [f"host{i}.example.com" for i in range(1, 11)]
        hosts_file.write_text("\n".join(hosts) + "\n")
        mock_args_vault.host = str(hosts_file)

        mock_run_ssh = mocker.patch("fleetroll.commands.vault.run_ssh")
        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(str(a[0])))

        cmd_host_set_vault(mock_args_vault)

        mock_run_ssh.assert_not_called()
        output = "\n".join(captured)
        assert "Hosts file:" in output
        assert " 10" in output
        assert "  - host1.example.com" in output
        assert "  - host2.example.com" in output
        assert "  - host3.example.com" in output
        assert "  - host4.example.com" in output
        assert "  - host5.example.com" in output
        assert "  - host6.example.com" not in output
        assert "... and 5 more hosts" in output
