"""Type definitions for CLI command arguments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class HostAuditArgs:
    """Arguments for host-audit command."""

    host: str
    ssh_option: list[str] | None
    connect_timeout: int
    timeout: int
    audit_log: str | None
    json: bool
    override_path: str
    role_path: str
    vault_path: str
    no_content: bool
    workers: int
    batch_timeout: int
    verbose: bool
    quiet: bool


@dataclass
class HostMonitorArgs:
    """Arguments for host-monitor command."""

    host: str
    audit_log: str | None
    json: bool
    override_path: str
    role_path: str
    vault_path: str
    once: bool


@dataclass
class OverrideShowArgs:
    """Arguments for show-override command."""

    sha_prefix: str
    audit_log: str | None


@dataclass
class VaultShowArgs:
    """Arguments for show-vault command."""

    sha_prefix: str
    audit_log: str | None


@dataclass
class HostSetOverrideArgs:
    """Arguments for host-set-override command."""

    host: str
    ssh_option: list[str] | None
    connect_timeout: int
    timeout: int
    audit_log: str | None
    json: bool
    workers: int
    override_path: str
    from_file: str | None
    validate: bool
    mode: str
    owner: str
    group: str
    no_backup: bool
    reason: str | None
    confirm: bool


@dataclass
class HostSetVaultArgs:
    """Arguments for host-set-vault command."""

    host: str
    ssh_option: list[str] | None
    connect_timeout: int
    timeout: int
    audit_log: str | None
    json: bool
    workers: int
    vault_path: str
    from_file: str | None
    mode: str
    owner: str
    group: str
    no_backup: bool
    reason: str | None
    confirm: bool


@dataclass
class HostUnsetOverrideArgs:
    """Arguments for host-unset-override command."""

    host: str
    ssh_option: list[str] | None
    connect_timeout: int
    timeout: int
    audit_log: str | None
    json: bool
    workers: int
    override_path: str
    no_backup: bool
    reason: str | None
    confirm: bool


@dataclass
class TcFetchArgs:
    """Arguments for tc-fetch command."""

    host: str
    verbose: int
    quiet: bool


class HasSshOptions(Protocol):
    """Protocol for args that contain SSH connection options."""

    connect_timeout: int
    ssh_option: list[str] | None


class HasAuditPaths(Protocol):
    """Protocol for args that contain audit file paths."""

    override_path: str
    role_path: str
