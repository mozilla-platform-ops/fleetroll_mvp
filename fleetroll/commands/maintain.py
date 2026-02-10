"""FleetRoll maintenance command."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import click

from ..constants import AUDIT_FILE_NAME, DB_FILE_NAME
from ..db import compact_database
from ..utils import default_audit_log_path

if TYPE_CHECKING:
    from ..cli_types import MaintainArgs


def rotate_log_file(
    log_path: Path, *, dry_run: bool = False, threshold_mb: int = 100, force: bool = False
) -> tuple[bool, str]:
    """Rotate a single log file by renaming it with timestamp.

    Args:
        log_path: Path to log file to rotate
        dry_run: If True, don't actually rotate
        threshold_mb: Only rotate files >= this size in MB
        force: If True, rotate regardless of size

    Returns:
        Tuple of (rotated, message) where rotated is True if file was/would be rotated
    """
    if not log_path.exists():
        return (False, f"SKIP {log_path.name}: file does not exist")

    size_mb = log_path.stat().st_size / (1024 * 1024)

    if not force and size_mb < threshold_mb:
        return (
            False,
            f"SKIP {log_path.name}: {size_mb:.1f} MB (below {threshold_mb} MB threshold)",
        )

    # Generate archive name: audit.jsonl -> audit.jsonl.20260128-215930
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    archive_path = log_path.parent / f"{log_path.name}.{timestamp}"

    if dry_run:
        return (
            True,
            f"DRY RUN: Would rotate {log_path.name} ({size_mb:.1f} MB) -> {archive_path.name}",
        )

    try:
        shutil.move(str(log_path), str(archive_path))
        return (True, f"OK {log_path.name} ({size_mb:.1f} MB) -> {archive_path.name}")
    except Exception as e:
        return (False, f"FAIL {log_path.name}: {e}")


def cmd_maintain(args: MaintainArgs) -> None:
    """Maintain FleetRoll data files.

    Rotates audit log and compacts SQLite database to prevent unbounded growth.
    Creates timestamped archives and starts fresh log files.
    Does NOT backfill - new files start empty.
    """
    audit_log = Path(args.audit_log) if args.audit_log else default_audit_log_path()
    fleetroll_dir = audit_log.parent

    # Files to consider for rotation
    log_files = [
        fleetroll_dir / AUDIT_FILE_NAME,
    ]

    if not args.confirm:
        click.echo("DRY RUN: --confirm not provided; no changes will be made.")
        click.echo(f"FleetRoll directory: {fleetroll_dir}")
        click.echo()

    # Rotate log files
    results = []
    for log_path in log_files:
        rotated, message = rotate_log_file(log_path, dry_run=not args.confirm, force=args.force)
        results.append((rotated, message))
        click.echo(message)

    # Compact database
    db_path = fleetroll_dir / DB_FILE_NAME
    if db_path.exists():
        if args.confirm:
            size_before, size_after = compact_database(db_path)
            size_before_mb = size_before / (1024 * 1024)
            size_after_mb = size_after / (1024 * 1024)
            reduction = size_before - size_after
            reduction_mb = reduction / (1024 * 1024)
            pct = (reduction / size_before * 100) if size_before > 0 else 0
            click.echo(
                f"OK {DB_FILE_NAME}: compacted from {size_before_mb:.1f} MB to {size_after_mb:.1f} MB "
                f"(freed {reduction_mb:.1f} MB, {pct:.1f}%)"
            )
        else:
            size_mb = db_path.stat().st_size / (1024 * 1024)
            click.echo(f"DRY RUN: Would compact {DB_FILE_NAME} (currently {size_mb:.1f} MB)")
    else:
        click.echo(f"SKIP {DB_FILE_NAME}: file does not exist")

    if not args.confirm:
        click.echo()
        click.echo("Run again with --confirm to apply changes.")
        return

    # Summary
    rotated_count = sum(1 for rotated, _ in results if rotated)
    click.echo()
    if rotated_count > 0:
        click.echo(f"Summary: {rotated_count} file(s) rotated, database compacted.")
        click.echo("New log files will be created on next write.")
    else:
        click.echo("Summary: No files rotated, database compacted.")
