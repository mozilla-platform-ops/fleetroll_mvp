"""Shared pytest fixtures for FleetRoll tests."""

from __future__ import annotations

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from fleetroll.cli_types import (
    HostAuditArgs,
    HostSetOverrideArgs,
    HostSetVaultArgs,
    HostUnsetOverrideArgs,
)
from fleetroll.constants import CONTENT_SENTINEL


@pytest.fixture
def tmp_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def tmp_audit_log(tmp_dir: Path) -> Path:
    """Create a temporary audit log path."""
    return tmp_dir / ".fleetroll" / "audit.jsonl"


@pytest.fixture
def tmp_host_file(tmp_dir: Path) -> Path:
    """Create a temporary host list file with sample hosts."""
    path = tmp_dir / "hosts.txt"
    path.write_text(
        "host1.example.com\nhost2.example.com\n# this is a comment\n\nhost3.example.com\n"
    )
    return path


@pytest.fixture
def mock_args_audit(tmp_audit_log: Path) -> HostAuditArgs:
    """Create Args object for audit command."""
    return HostAuditArgs(
        host="test.example.com",
        ssh_option=None,
        connect_timeout=10,
        timeout=60,
        audit_log=str(tmp_audit_log),
        json=False,
        no_content=False,
        workers=10,
        batch_timeout=600,
        verbose=False,
        quiet=False,
    )


@pytest.fixture
def mock_args_set(tmp_audit_log: Path) -> HostSetOverrideArgs:
    """Create Args object for set command."""
    return HostSetOverrideArgs(
        host="test.example.com",
        ssh_option=None,
        connect_timeout=10,
        timeout=60,
        audit_log=str(tmp_audit_log),
        json=False,
        workers=10,
        from_file=None,
        validate=True,
        mode="0644",
        owner="root",
        group="root",
        no_backup=False,
        reason="test reason",
        confirm=True,
    )


@pytest.fixture
def mock_args_unset(tmp_audit_log: Path) -> HostUnsetOverrideArgs:
    """Create Args object for unset command."""
    return HostUnsetOverrideArgs(
        host="test.example.com",
        ssh_option=None,
        connect_timeout=10,
        timeout=60,
        audit_log=str(tmp_audit_log),
        json=False,
        workers=10,
        no_backup=False,
        reason="test reason",
        confirm=True,
    )


@pytest.fixture
def mock_args_vault(tmp_audit_log: Path) -> HostSetVaultArgs:
    """Create Args object for vault set command."""
    return HostSetVaultArgs(
        host="test.example.com",
        ssh_option=None,
        connect_timeout=10,
        timeout=60,
        audit_log=str(tmp_audit_log),
        json=False,
        workers=10,
        from_file=None,
        validate=True,
        mode="0640",
        owner="root",
        group="root",
        no_backup=False,
        reason="test reason",
        confirm=True,
    )


@pytest.fixture
def sample_audit_output() -> str:
    """Sample SSH output from successful audit command."""
    return f"""ROLE_PRESENT=1
ROLE=test-role
OVERRIDE_PRESENT=1
OVERRIDE_MODE=644
OVERRIDE_OWNER=root
OVERRIDE_GROUP=root
OVERRIDE_SIZE=123
OVERRIDE_MTIME=1704067200
{CONTENT_SENTINEL}
key1=value1
key2=value2
"""


@pytest.fixture
def sample_audit_output_no_override() -> str:
    """Sample SSH output with no override file."""
    return """ROLE_PRESENT=1
ROLE=test-role
OVERRIDE_PRESENT=0
"""


@pytest.fixture
def temp_db() -> Generator[Path, None, None]:
    """Create a temporary SQLite database for testing."""
    from fleetroll.db import init_db

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    try:
        init_db(db_path)
        yield db_path
    finally:
        # Clean up database and WAL files
        db_path.unlink(missing_ok=True)
        Path(f"{db_path}-wal").unlink(missing_ok=True)
        Path(f"{db_path}-shm").unlink(missing_ok=True)
