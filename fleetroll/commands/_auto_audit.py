"""Shared auto-audit helper for host-set-override, host-unset-override, host-set-vault, and host-run-puppet."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Protocol

import click

logger = logging.getLogger(__name__)


class HasAutoAuditArgs(Protocol):
    """Protocol for args objects that support auto-audit."""

    ssh_option: list[str] | None
    connect_timeout: int
    timeout: int
    workers: int
    no_audit: bool


def _run_auto_audit(hosts: list[str], args: HasAutoAuditArgs, audit_log: Path) -> None:
    """Run gather-host on the given hosts to refresh DB observations."""
    from ..cli_types import HostAuditArgs
    from .gather_host import cmd_host_audit_batch

    audit_args = HostAuditArgs(
        host="",
        ssh_option=args.ssh_option,
        connect_timeout=args.connect_timeout,
        timeout=args.timeout,
        audit_log=str(audit_log),
        json=False,
        no_content=False,
        workers=args.workers,
        batch_timeout=600,
        verbose=False,
        quiet=True,
    )
    cmd_host_audit_batch(hosts, audit_args)
    print(f"Audit refreshed for {len(hosts)} host(s).")


def _maybe_auto_audit(hosts: list[str], args: HasAutoAuditArgs, audit_log: Path) -> None:
    """Run auto-audit if not suppressed; log warnings on failure."""
    if args.no_audit:
        return
    try:
        _run_auto_audit(hosts, args, audit_log)
    except Exception as exc:
        logger.warning("Auto-audit failed: %s", exc)
        click.echo(
            click.style(f"Warning: auto-audit failed: {exc}", fg="yellow"),
            file=sys.stderr,
        )
