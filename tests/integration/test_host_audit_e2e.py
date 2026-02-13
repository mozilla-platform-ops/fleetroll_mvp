"""E2E integration tests for host-audit command.

Tests the full audit pipeline by running against a real SSH server in Docker:
- SSH connection with real keys
- Remote audit script execution
- Result parsing
- SQLite storage
- Override file content storage
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fleetroll.cli_types import HostAuditArgs
from fleetroll.commands.audit import cmd_host_audit
from fleetroll.db import get_connection, get_db_path, get_latest_host_observations


@pytest.mark.integration
def test_audit_succeeds(
    sshd_container: dict[str, str | int],
    audit_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that basic audit completes without error."""
    # Build SSH target string with custom port
    host = f"{sshd_container['user']}@{sshd_container['host']}"
    ssh_opts = [
        f"-p {sshd_container['port']}",
        f"-i {sshd_container['key_path']}",
        "-o StrictHostKeyChecking=no",
        "-o UserKnownHostsFile=/dev/null",
        "-o BatchMode=yes",
    ]

    args = HostAuditArgs(
        host=host,
        ssh_option=ssh_opts,
        connect_timeout=10,
        timeout=30,
        audit_log=None,  # Use default ~/.fleetroll/audit.jsonl
        json=False,
        no_content=False,
        workers=1,
        batch_timeout=600,
        verbose=False,
        quiet=True,
    )

    # Should not raise
    cmd_host_audit(args)

    # Verify database entry was created
    db_path = get_db_path()
    assert db_path.exists()
    conn = get_connection(db_path)
    latest, _ = get_latest_host_observations(conn, [host])
    conn.close()

    assert host in latest
    assert latest[host]["ok"] is True


@pytest.mark.integration
def test_audit_detects_role(
    sshd_container: dict[str, str | int],
    audit_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that audit correctly reads /etc/puppet_role."""
    host = f"{sshd_container['user']}@{sshd_container['host']}"
    ssh_opts = [
        f"-p {sshd_container['port']}",
        f"-i {sshd_container['key_path']}",
        "-o StrictHostKeyChecking=no",
        "-o UserKnownHostsFile=/dev/null",
        "-o BatchMode=yes",
    ]

    args = HostAuditArgs(
        host=host,
        ssh_option=ssh_opts,
        connect_timeout=10,
        timeout=30,
        audit_log=None,
        json=False,
        no_content=False,
        workers=1,
        batch_timeout=600,
        verbose=False,
        quiet=True,
    )

    cmd_host_audit(args)

    db_path = get_db_path()
    conn = get_connection(db_path)
    latest, _ = get_latest_host_observations(conn, [host])
    conn.close()

    obs = latest[host]["observed"]
    assert obs["role_present"] is True
    assert obs["role"] == "gecko-t-linux-talos"


@pytest.mark.integration
def test_audit_detects_override(
    sshd_container: dict[str, str | int],
    audit_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that audit detects override file and reads metadata."""
    host = f"{sshd_container['user']}@{sshd_container['host']}"
    ssh_opts = [
        f"-p {sshd_container['port']}",
        f"-i {sshd_container['key_path']}",
        "-o StrictHostKeyChecking=no",
        "-o UserKnownHostsFile=/dev/null",
        "-o BatchMode=yes",
    ]

    args = HostAuditArgs(
        host=host,
        ssh_option=ssh_opts,
        connect_timeout=10,
        timeout=30,
        audit_log=None,
        json=False,
        no_content=False,
        workers=1,
        batch_timeout=600,
        verbose=False,
        quiet=True,
    )

    cmd_host_audit(args)

    db_path = get_db_path()
    conn = get_connection(db_path)
    latest, _ = get_latest_host_observations(conn, [host])
    conn.close()

    obs = latest[host]["observed"]
    assert obs["override_present"] is True
    assert obs["override_sha256"] is not None
    assert len(obs["override_sha256"]) == 64  # SHA256 hex length

    meta = obs["override_meta"]
    assert meta["mode"] == "644"
    assert meta["owner"] == "testuser"
    assert meta["size"] > 0


@pytest.mark.integration
def test_audit_stores_override_file(
    sshd_container: dict[str, str | int],
    audit_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that override content is stored to ~/.fleetroll/overrides/."""
    host = f"{sshd_container['user']}@{sshd_container['host']}"
    ssh_opts = [
        f"-p {sshd_container['port']}",
        f"-i {sshd_container['key_path']}",
        "-o StrictHostKeyChecking=no",
        "-o UserKnownHostsFile=/dev/null",
        "-o BatchMode=yes",
    ]

    args = HostAuditArgs(
        host=host,
        ssh_option=ssh_opts,
        connect_timeout=10,
        timeout=30,
        audit_log=None,
        json=False,
        no_content=False,
        workers=1,
        batch_timeout=600,
        verbose=False,
        quiet=True,
    )

    cmd_host_audit(args)

    db_path = get_db_path()
    conn = get_connection(db_path)
    latest, _ = get_latest_host_observations(conn, [host])
    conn.close()

    obs = latest[host]["observed"]
    override_sha = obs["override_sha256"]

    # Check that override file was stored
    overrides_dir = audit_home / ".fleetroll" / "overrides"
    stored_file = overrides_dir / override_sha[:12]
    assert stored_file.exists()

    # Verify content matches expected fixture
    content = stored_file.read_text()
    assert "key1=value1" in content
    assert "key2=value2" in content


@pytest.mark.integration
def test_audit_parses_puppet_metadata_json(
    sshd_container: dict[str, str | int],
    audit_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that puppet metadata JSON is parsed correctly."""
    host = f"{sshd_container['user']}@{sshd_container['host']}"
    ssh_opts = [
        f"-p {sshd_container['port']}",
        f"-i {sshd_container['key_path']}",
        "-o StrictHostKeyChecking=no",
        "-o UserKnownHostsFile=/dev/null",
        "-o BatchMode=yes",
    ]

    args = HostAuditArgs(
        host=host,
        ssh_option=ssh_opts,
        connect_timeout=10,
        timeout=30,
        audit_log=None,
        json=False,
        no_content=False,
        workers=1,
        batch_timeout=600,
        verbose=False,
        quiet=True,
    )

    cmd_host_audit(args)

    db_path = get_db_path()
    conn = get_connection(db_path)
    latest, _ = get_latest_host_observations(conn, [host])
    conn.close()

    obs = latest[host]["observed"]
    puppet_meta = obs.get("puppet_metadata")

    assert puppet_meta is not None
    assert puppet_meta["environment"] == "production"
    assert puppet_meta["git_branch"] == "main"
    assert "test_commit_sha_not_a_real_hex_value" in puppet_meta["git_sha"]


@pytest.mark.integration
def test_audit_detects_vault(
    sshd_container: dict[str, str | int],
    audit_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that audit detects vault.yaml presence and metadata."""
    host = f"{sshd_container['user']}@{sshd_container['host']}"
    ssh_opts = [
        f"-p {sshd_container['port']}",
        f"-i {sshd_container['key_path']}",
        "-o StrictHostKeyChecking=no",
        "-o UserKnownHostsFile=/dev/null",
        "-o BatchMode=yes",
    ]

    args = HostAuditArgs(
        host=host,
        ssh_option=ssh_opts,
        connect_timeout=10,
        timeout=30,
        audit_log=None,
        json=False,
        no_content=False,
        workers=1,
        batch_timeout=600,
        verbose=False,
        quiet=True,
    )

    cmd_host_audit(args)

    db_path = get_db_path()
    conn = get_connection(db_path)
    latest, _ = get_latest_host_observations(conn, [host])
    conn.close()

    obs = latest[host]["observed"]
    assert obs.get("vault_present") is True
    assert obs.get("vault_sha256") is not None
    assert len(obs["vault_sha256"]) == 64  # SHA256 hex length

    vault_meta = obs.get("vault_meta")
    assert vault_meta is not None
    assert vault_meta["mode"] == "640"
    assert vault_meta["owner"] == "root"


@pytest.mark.integration
def test_audit_detects_os_type(
    sshd_container: dict[str, str | int],
    audit_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that audit detects OS type as Linux."""
    host = f"{sshd_container['user']}@{sshd_container['host']}"
    ssh_opts = [
        f"-p {sshd_container['port']}",
        f"-i {sshd_container['key_path']}",
        "-o StrictHostKeyChecking=no",
        "-o UserKnownHostsFile=/dev/null",
        "-o BatchMode=yes",
    ]

    args = HostAuditArgs(
        host=host,
        ssh_option=ssh_opts,
        connect_timeout=10,
        timeout=30,
        audit_log=None,
        json=False,
        no_content=False,
        workers=1,
        batch_timeout=600,
        verbose=False,
        quiet=True,
    )

    cmd_host_audit(args)

    db_path = get_db_path()
    conn = get_connection(db_path)
    latest, _ = get_latest_host_observations(conn, [host])
    conn.close()

    obs = latest[host]["observed"]
    assert obs.get("os_type") == "Linux"


@pytest.mark.integration
def test_audit_detects_uptime(
    sshd_container: dict[str, str | int],
    audit_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that audit detects uptime_s as positive integer."""
    host = f"{sshd_container['user']}@{sshd_container['host']}"
    ssh_opts = [
        f"-p {sshd_container['port']}",
        f"-i {sshd_container['key_path']}",
        "-o StrictHostKeyChecking=no",
        "-o UserKnownHostsFile=/dev/null",
        "-o BatchMode=yes",
    ]

    args = HostAuditArgs(
        host=host,
        ssh_option=ssh_opts,
        connect_timeout=10,
        timeout=30,
        audit_log=None,
        json=False,
        no_content=False,
        workers=1,
        batch_timeout=600,
        verbose=False,
        quiet=True,
    )

    cmd_host_audit(args)

    db_path = get_db_path()
    conn = get_connection(db_path)
    latest, _ = get_latest_host_observations(conn, [host])
    conn.close()

    obs = latest[host]["observed"]
    uptime_s = obs.get("uptime_s")
    assert uptime_s is not None
    assert isinstance(uptime_s, int)
    assert uptime_s > 0


@pytest.mark.integration
def test_audit_no_content_mode(
    sshd_container: dict[str, str | int],
    audit_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that --no-content skips override content but still detects presence."""
    host = f"{sshd_container['user']}@{sshd_container['host']}"
    ssh_opts = [
        f"-p {sshd_container['port']}",
        f"-i {sshd_container['key_path']}",
        "-o StrictHostKeyChecking=no",
        "-o UserKnownHostsFile=/dev/null",
        "-o BatchMode=yes",
    ]

    args = HostAuditArgs(
        host=host,
        ssh_option=ssh_opts,
        connect_timeout=10,
        timeout=30,
        audit_log=None,
        json=False,
        no_content=True,  # Skip content fetch
        workers=1,
        batch_timeout=600,
        verbose=False,
        quiet=True,
    )

    cmd_host_audit(args)

    db_path = get_db_path()
    conn = get_connection(db_path)
    latest, _ = get_latest_host_observations(conn, [host])
    conn.close()

    obs = latest[host]["observed"]
    assert obs["override_present"] is True
    # With --no-content, SHA is None and content is not stored
    assert obs.get("override_sha256") is None
    assert "override_contents_for_display" not in obs

    # Verify no override file was stored
    overrides_dir = audit_home / ".fleetroll" / "overrides"
    if overrides_dir.exists():
        assert len(list(overrides_dir.iterdir())) == 0
