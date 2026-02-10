"""TaskCluster worker data fetch command."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from ..constants import ROLE_TO_TASKCLUSTER
from ..exceptions import FleetRollError
from ..taskcluster import fetch_workers, load_tc_credentials
from ..utils import (
    format_elapsed_time,
    is_host_file,
    parse_host_list,
    utc_now_iso,
)

if TYPE_CHECKING:
    from ..cli_types import TcFetchArgs

logger = logging.getLogger(__name__)


def format_status_indicator(emoji: str, status: str, color: str) -> str:
    """Format colored emoji + status text.

    Args:
        emoji: The status emoji (✓, ⚠, ✗)
        status: Status text (SUCCESS, WARNING, FAILED)
        color: Click color name (green, yellow, red)

    Returns:
        Colored formatted string
    """
    return click.style(f"{emoji} {status}", fg=color)


def format_tc_fetch_quiet(
    *,
    worker_count: int,
    scan_count: int,
    warnings: list[str],
    elapsed_seconds: float,
) -> str:
    """Format tc-fetch output in quiet mode.

    Args:
        worker_count: Number of worker records written
        scan_count: Number of scan records written
        warnings: List of warning messages
        elapsed_seconds: Elapsed time in seconds

    Returns:
        Single-line formatted output
    """
    elapsed = format_elapsed_time(elapsed_seconds)

    if warnings:
        status = format_status_indicator("⚠", "WARNING", "yellow")
        warning_text = " (" + ", ".join(warnings) + ")"
    else:
        status = format_status_indicator("✓", "SUCCESS", "green")
        warning_text = ""

    return (
        f"{status} Wrote {worker_count} worker(s), {scan_count} scan(s){warning_text} ({elapsed})"
    )


def get_host_roles_bulk(hosts: set[str], db_conn: sqlite3.Connection) -> dict[str, str | None]:
    """Get the most recent role for multiple hosts from SQLite.

    Args:
        hosts: Set of hostnames to look up
        db_conn: SQLite database connection

    Returns:
        Dict mapping hostname to role string (or None if not found)
    """
    from ..db import get_latest_host_observations

    latest, _ = get_latest_host_observations(db_conn, list(hosts))

    result = dict.fromkeys(hosts)
    for host, record in latest.items():
        observed = record.get("observed", {})
        role = observed.get("role")
        if role:
            result[host] = role

    return result


def strip_fqdn(hostname: str) -> str:
    """Strip FQDN to get short hostname.

    Args:
        hostname: Full hostname (e.g., "t-linux64-ms-016.test.releng.mdc1.mozilla.com")

    Returns:
        Short hostname (e.g., "t-linux64-ms-016")
    """
    return hostname.split(".")[0]


def build_role_to_hosts_mapping(host_to_role: dict[str, str | None]) -> dict[str, list[str]]:
    """Build inverted mapping from roles to lists of hosts.

    Args:
        host_to_role: Mapping of hostname to role (or None if no role found)

    Returns:
        Dictionary mapping each role to list of hosts with that role
    """
    role_to_hosts: dict[str, list[str]] = defaultdict(list)
    for host, role in host_to_role.items():
        if role:
            role_to_hosts[role].append(host)
    return dict(role_to_hosts)


def map_roles_to_worker_types(
    role_to_hosts: dict[str, list[str]],
    role_lookup: dict[str, tuple[str, str]],
) -> tuple[dict[str, tuple[str, str]], dict[tuple[str, str], list[str]], list[tuple[str, int]]]:
    """Map roles to TaskCluster worker types.

    Args:
        role_to_hosts: Mapping of role to list of hosts
        role_lookup: Role to (provisioner, worker_type) lookup table

    Returns:
        Tuple of:
        - role_to_worker_type: Mapping of role to (provisioner, worker_type)
        - worker_type_to_hosts: Mapping of (provisioner, worker_type) to list of hosts
        - unmapped_roles: List of (role, host_count) tuples for roles not in lookup table
    """
    role_to_worker_type: dict[str, tuple[str, str]] = {}
    worker_type_to_hosts: dict[tuple[str, str], list[str]] = defaultdict(list)
    unmapped_roles: list[tuple[str, int]] = []

    for role, role_hosts in role_to_hosts.items():
        if role not in role_lookup:
            unmapped_roles.append((role, len(role_hosts)))
            continue

        provisioner, worker_type = role_lookup[role]

        # Auto-convert role name to workerType if specified
        if worker_type == "AUTO_under_to_dash":
            worker_type = role.replace("_", "-")

        role_to_worker_type[role] = (provisioner, worker_type)
        worker_type_key = (provisioner, worker_type)
        worker_type_to_hosts[worker_type_key].extend(role_hosts)

    return role_to_worker_type, dict(worker_type_to_hosts), unmapped_roles


def match_workers_to_hosts(
    hosts: list[str],
    *,
    host_to_role: dict[str, str | None],
    role_to_worker_type: dict[str, tuple[str, str]],
    worker_type_to_workers: dict[tuple[str, str], dict[str, Any]],
    ts: str,
) -> list[dict[str, Any]]:
    """Match TaskCluster worker data to hosts and build worker records.

    Args:
        hosts: List of hostnames to match
        host_to_role: Mapping of hostname to role
        role_to_worker_type: Mapping of role to (provisioner, worker_type)
        worker_type_to_workers: Mapping of (provisioner, worker_type) to worker data
        ts: Timestamp for the records

    Returns:
        List of worker record dictionaries ready to write to JSONL
    """
    records = []

    for host in hosts:
        role = host_to_role.get(host)
        if not role or role not in role_to_worker_type:
            continue

        provisioner, worker_type = role_to_worker_type[role]
        worker_type_key = (provisioner, worker_type)
        workers_map = worker_type_to_workers.get(worker_type_key, {})

        # Match by short hostname
        short_host = strip_fqdn(host)
        worker_data = workers_map.get(short_host)

        if worker_data:
            # Extract fields from GraphQL response
            state = worker_data.get("state")
            last_date_active = worker_data.get("lastDateActive")
            quarantine_until = worker_data.get("quarantineUntil")

            # Extract task data from latestTask.run (GraphQL structure)
            task_started = None
            task_resolved = None
            task_state = None
            latest_task = worker_data.get("latestTask")
            if latest_task:
                run = latest_task.get("run")
                if run:
                    task_started = run.get("started")
                    task_resolved = run.get("resolved")
                    task_state = run.get("state")

            record = {
                "type": "worker",
                "ts": ts,
                "host": host,
                "worker_id": short_host,
                "provisioner": provisioner,
                "worker_type": worker_type,
                "state": state,
                "last_date_active": last_date_active,
                "task_started": task_started,
                "task_resolved": task_resolved,
                "task_state": task_state,
                "quarantine_until": quarantine_until,
            }
            records.append(record)

    return records


def cmd_tc_fetch(args: TcFetchArgs) -> None:
    """Fetch TaskCluster worker data for hosts.

    Args:
        args: Command arguments containing:
            - host: Single host or file with host list
            - verbose: Verbosity level (0=none, 1=verbose, 2+=very verbose)
            - quiet: Single-line output mode
    """
    start_time = time.time()
    verbose = getattr(args, "verbose", 0)
    quiet = getattr(args, "quiet", False)

    # Track warnings for quiet mode
    hosts_without_roles = 0
    api_errors = 0

    # Load credentials
    credentials = load_tc_credentials()

    # Parse host list
    hosts = []
    if is_host_file(args.host):
        hosts = parse_host_list(Path(args.host))
    else:
        hosts = [args.host]

    if not quiet:
        click.echo(f"Fetching TaskCluster data for {len(hosts)} host(s)...")

    # Initialize SQLite database
    from ..db import get_connection, get_db_path, init_db, insert_tc_worker

    db_path = get_db_path()
    init_db(db_path)
    db_conn = get_connection(db_path)

    try:
        # Map hosts to roles from SQLite
        host_to_role = get_host_roles_bulk(set(hosts), db_conn)

        # Track hosts without roles for logging
        for host, role in host_to_role.items():
            if role:
                if verbose >= 1 and not quiet:
                    click.echo(f"  {host} -> role: {role}")
            else:
                hosts_without_roles += 1
                if verbose >= 1 and not quiet:
                    click.echo(f"  {host} -> role: NOT FOUND")

        # Build role to hosts mapping
        role_to_hosts = build_role_to_hosts_mapping(host_to_role)

        # Map roles to workerTypes
        role_to_worker_type, worker_type_to_hosts, unmapped_roles = map_roles_to_worker_types(
            role_to_hosts, ROLE_TO_TASKCLUSTER
        )

        # Display warnings for unmapped roles
        for role, host_count in unmapped_roles:
            if not quiet:
                click.echo(
                    f"WARNING: Role '{role}' not found in lookup table, skipping {host_count} host(s)",
                    err=True,
                )

        if not worker_type_to_hosts:
            if not quiet:
                click.echo("No hosts with mappable roles found. Nothing to fetch.")
            return

        # Show summary of what we're fetching
        if not quiet:
            role_summary = ", ".join(
                f"{role} ({len(hosts)} host(s))"
                for role, hosts in role_to_hosts.items()
                if role in role_to_worker_type
            )
            click.echo(f"Found roles: {role_summary}")

        # Fetch data for each workerType
        ts = utc_now_iso()
        worker_type_to_workers: dict[tuple[str, str], dict[str, Any]] = {}

        for provisioner, worker_type in worker_type_to_hosts:
            if not quiet:
                click.echo(f"Querying workerType {provisioner}/{worker_type}...", nl=False)
            try:
                workers_list = fetch_workers(
                    provisioner, worker_type, credentials, verbose=(verbose >= 2 and not quiet)
                )

                if verbose >= 2 and not quiet:
                    click.echo(f"\n  Raw API response: {len(workers_list)} worker(s)")
                    if workers_list and len(workers_list) <= 3:
                        # Show first few workers in full
                        for i, worker in enumerate(workers_list[:3]):
                            click.echo(f"  Worker {i}: {json.dumps(worker, indent=2)}")
                    elif workers_list:
                        # Show just first worker and available keys
                        click.echo(f"  First worker keys: {list(workers_list[0].keys())}")
                        click.echo(
                            f"  First worker sample: {json.dumps(workers_list[0], indent=2)}"
                        )

                # Build worker_id -> worker data map
                workers_map = {}
                for worker in workers_list:
                    worker_id = worker.get("workerId")
                    if worker_id:
                        workers_map[worker_id] = worker

                worker_type_to_workers[(provisioner, worker_type)] = workers_map
                if not quiet:
                    click.echo(f" {len(workers_map)} worker(s) found")

            except FleetRollError as e:
                api_errors += 1
                if not quiet:
                    click.echo(f" FAILED: {e}", err=True)
                # Store empty result so we still write scan record
                worker_type_to_workers[(provisioner, worker_type)] = {}

        worker_records_written = 0
        scan_records_written = 0

        # Match workers to hosts
        worker_records = match_workers_to_hosts(
            hosts,
            host_to_role=host_to_role,
            role_to_worker_type=role_to_worker_type,
            worker_type_to_workers=worker_type_to_workers,
            ts=ts,
        )

        # Write worker records to SQLite
        for record in worker_records:
            host = record["host"]
            short_host = record["worker_id"]
            role = host_to_role.get(host)

            if verbose >= 1 and not quiet:
                provisioner = record["provisioner"]
                worker_type = record["worker_type"]
                worker_type_key = (provisioner, worker_type)
                workers_map = worker_type_to_workers.get(worker_type_key, {})

                click.echo(f"  Matching {host} (short: {short_host})...")
                click.echo(f"    Found worker data for {short_host}")
                if verbose >= 2:
                    worker_data = workers_map.get(short_host)
                    click.echo(f"    Worker data: {json.dumps(worker_data, indent=4)}")

            # Insert into SQLite
            insert_tc_worker(db_conn, record)
            worker_records_written += 1

            if verbose >= 1 and not quiet:
                state = record["state"]
                quarantine = record["quarantine_until"]
                click.echo(f"    Wrote record: state={state}, quarantine={quarantine}")

        # Commit all inserts
        db_conn.commit()

        # Log skipped hosts
        if verbose >= 1 and not quiet:
            written_hosts = {r["host"] for r in worker_records}
            for host in hosts:
                if host not in written_hosts:
                    role = host_to_role.get(host)
                    if not role or role not in role_to_worker_type:
                        click.echo(f"  Skipping {host}: no role or role not in worker type mapping")
                    else:
                        click.echo(f"  Skipping {host}: no worker data found")

        # Count scan records for quiet-mode output compatibility
        # (scan records are not written to SQLite as they're never read)
        scan_records_written = len(worker_type_to_hosts)

        # Build warnings list for quiet mode
        elapsed_seconds = time.time() - start_time
        if quiet:
            warning_list = []
            if hosts_without_roles > 0:
                warning_list.append(f"{hosts_without_roles} hosts without role data")
            if api_errors > 0:
                warning_list.append(f"{api_errors} API errors" if api_errors > 1 else "1 API error")

            output = format_tc_fetch_quiet(
                worker_count=worker_records_written,
                scan_count=scan_records_written,
                warnings=warning_list,
                elapsed_seconds=elapsed_seconds,
            )
            click.echo(output)
        else:
            msg = (
                f"Wrote {worker_records_written} worker record(s) and "
                f"{scan_records_written} scan record(s) to {db_path}"
            )
            click.echo(msg)

            # Also refresh GitHub branch refs (throttled)
            from ..github import do_github_fetch

            do_github_fetch(quiet=quiet)
    finally:
        db_conn.close()
