"""TaskCluster worker data fetch command."""

from __future__ import annotations

import json
import logging
import sqlite3
import textwrap
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from ..constants import DEFAULT_TC_PROVISIONER, OVERRIDES_DIR_NAME, ROLE_TO_TASKCLUSTER
from ..exceptions import CommandFailureError, FleetRollError
from ..taskcluster import (
    create_tc_clients,
    fetch_worker_type_names,
    fetch_workers,
    load_tc_credentials,
)
from ..tc_lookup import build_lookup_attempts
from ..utils import (
    default_audit_log_path,
    format_elapsed_time,
    is_host_file,
    parse_host_list,
    utc_now_iso,
)
from .monitor.cache import parse_override_file

if TYPE_CHECKING:
    from ..cli_types import TcFetchArgs

logger = logging.getLogger(__name__)


def build_task_run_cache(
    tc_records: dict[str, dict[str, Any]],
) -> dict[tuple[str, int], dict[str, Any]]:
    """Build a cache of resolved task runs from the latest stored worker records."""
    result: dict[tuple[str, int], dict[str, Any]] = {}
    for record in tc_records.values():
        task_id = record.get("task_id")
        run_id = record.get("task_run_id")
        resolved = record.get("task_resolved")
        if not isinstance(task_id, str) or not isinstance(run_id, int) or not resolved:
            continue
        result[(task_id, run_id)] = {
            "started": record.get("task_started"),
            "resolved": resolved,
            "state": record.get("task_state"),
        }
    return result


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
    host_count: int,
    warnings: bool,
    elapsed_seconds: float,
    failed: bool = False,
) -> str:
    """Format gather-tc output in quiet mode.

    Args:
        worker_count: Number of worker records written
        host_count: Number of requested hosts
        warnings: Whether collection has non-fatal warnings
        elapsed_seconds: Elapsed time in seconds

    Returns:
        Single-line output capped at 80 visible characters
    """
    elapsed = format_elapsed_time(elapsed_seconds)

    if failed:
        status = format_status_indicator("✗", "FAILED", "red")
    elif warnings:
        status = format_status_indicator("⚠", "WARNING", "yellow")
    else:
        status = format_status_indicator("✓", "SUCCESS", "green")
    body = f"TC {worker_count}/{host_count} collected ({elapsed})"
    if failed or warnings:
        body += "; -v for details"
    body = textwrap.shorten(body, width=80 - len(click.unstyle(status)) - 1, placeholder="…")
    return f"{status} {body}"


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


def get_host_override_shas_bulk(
    hosts: set[str], db_conn: sqlite3.Connection
) -> dict[str, str | None]:
    """Get the most recent override file SHA256 for multiple hosts from SQLite.

    Args:
        hosts: Set of hostnames to look up
        db_conn: SQLite database connection

    Returns:
        Dict mapping hostname to override SHA256 string (or None if the host
        has no override deployed / no observation).
    """
    from ..db import get_latest_host_observations

    latest, _ = get_latest_host_observations(db_conn, list(hosts))

    result = dict.fromkeys(hosts)
    for host, record in latest.items():
        observed = record.get("observed", {})
        if observed.get("override_present"):
            result[host] = observed.get("override_sha256")

    return result


def resolve_override_pool(sha256: str | None, overrides_dir: Path) -> str | None:
    """Resolve a host's WORKER_TYPE_OVERRIDE pool from its override file.

    Override files are stored under ``overrides_dir`` keyed by the 12-char
    prefix of the content SHA256 (matching the audit storage scheme).

    Args:
        sha256: Full override file SHA256 (or None)
        overrides_dir: Directory containing stored override files

    Returns:
        The WORKER_TYPE_OVERRIDE pool name, or None if not set / file missing.
    """
    if not sha256:
        return None

    override_file = overrides_dir / sha256[:12]
    if not override_file.exists():
        return None

    info = parse_override_file(override_file)
    if not info:
        return None

    return info.get("worker_type_override")


def build_host_override_pools(
    host_to_override_sha: dict[str, str | None], overrides_dir: Path
) -> dict[str, str | None]:
    """Map hosts to their WORKER_TYPE_OVERRIDE pool (or None).

    Args:
        host_to_override_sha: Mapping of hostname to override SHA256 (or None)
        overrides_dir: Directory containing stored override files

    Returns:
        Dict mapping hostname to override pool name (or None if no override pool)
    """
    # Cache parsed results per SHA so we read each override file only once.
    pool_by_sha: dict[str, str | None] = {}
    result: dict[str, str | None] = {}
    for host, sha in host_to_override_sha.items():
        if not sha:
            result[host] = None
            continue
        if sha not in pool_by_sha:
            pool_by_sha[sha] = resolve_override_pool(sha, overrides_dir)
        result[host] = pool_by_sha[sha]
    return result


def resolve_host_worker_types(
    hosts: list[str],
    *,
    host_to_role: dict[str, str | None],
    role_to_worker_type: dict[str, tuple[str, str]],
    host_to_override_pool: dict[str, str | None],
    default_provisioner: str = DEFAULT_TC_PROVISIONER,
    observed_identities: dict[str, dict[str, str] | None] | None = None,
) -> dict[str, tuple[str, str]]:
    """Resolve each host's (provisioner, worker_type), applying overrides.

    Observed identity takes precedence, including explicit collection failure.
    Only hosts without identity collection metadata use the legacy baseline.
    When a legacy host has a
    WORKER_TYPE_OVERRIDE pool, it replaces the role-derived worker type while
    keeping the provisioner from the role mapping (or ``default_provisioner``
    if the role is unmapped). Hosts with neither a mapped role nor an override
    pool are omitted.

    Args:
        hosts: List of hostnames to resolve
        host_to_role: Mapping of hostname to role (or None)
        role_to_worker_type: Mapping of role to (provisioner, worker_type)
        host_to_override_pool: Mapping of hostname to override pool (or None)
        default_provisioner: Provisioner to use when override pool exists but
            role is unmapped
        observed_identities: Collected identities; None values block legacy fallback

    Returns:
        Dict mapping hostname to (provisioner, worker_type)
    """
    result: dict[str, tuple[str, str]] = {}
    for host in hosts:
        if observed_identities is not None and host in observed_identities:
            identity = observed_identities[host]
            if identity:
                result[host] = (identity["provisionerId"], identity["workerType"])
            continue
        role = host_to_role.get(host)
        base = role_to_worker_type.get(role) if role else None
        override_pool = host_to_override_pool.get(host)

        if override_pool:
            provisioner = base[0] if base else default_provisioner
            result[host] = (provisioner, override_pool)
        elif base:
            result[host] = base

    return result


def get_observed_identities(
    hosts: list[str], *, db_conn: sqlite3.Connection
) -> dict[str, dict[str, str] | None]:
    """Absent keys allow legacy fallback; explicit failed collection does not."""
    from ..db import get_latest_host_observations

    latest, _ = get_latest_host_observations(db_conn, hosts)
    result: dict[str, dict[str, str] | None] = {}
    keys = ("provisionerId", "workerType", "workerGroup", "workerId")
    for host, record in latest.items():
        observed = record.get("observed", {})
        if not record.get("ok"):
            result[host] = None
            continue
        if "tc_worker_identity_status" not in observed:
            continue
        result[host] = None
        identity = observed.get("tc_worker_identity")
        if (
            record.get("ok")
            and observed.get("tc_worker_identity_status") == "ok"
            and isinstance(identity, dict)
            and all(isinstance(identity.get(key), str) and identity[key] for key in keys)
        ):
            result[host] = {key: identity[key] for key in keys}
    return result


def index_workers(workers: list[dict[str, Any]]) -> dict[Any, Any]:
    """Index full identities; allow legacy short IDs only when unambiguous."""
    result: dict[Any, Any] = {}
    by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_identity: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for worker in workers:
        worker_id = worker.get("workerId")
        if worker_id:
            by_identity[(worker.get("workerGroup"), worker_id)].append(worker)
            by_id[worker_id].append(worker)
    result.update({key: values[0] for key, values in by_id.items() if len(values) == 1})
    result.update({key: values[0] for key, values in by_identity.items() if len(values) == 1})
    return result


def invert_host_worker_types(
    host_to_worker_type: dict[str, tuple[str, str]],
) -> dict[tuple[str, str], list[str]]:
    """Invert a host->worker_type map into worker_type->hosts.

    Args:
        host_to_worker_type: Mapping of hostname to (provisioner, worker_type)

    Returns:
        Dict mapping (provisioner, worker_type) to list of hosts
    """
    worker_type_to_hosts: dict[tuple[str, str], list[str]] = defaultdict(list)
    for host, worker_type_key in host_to_worker_type.items():
        worker_type_to_hosts[worker_type_key].append(host)
    return dict(worker_type_to_hosts)


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


def build_windows_role_mapping(
    provisioner: str,
    credentials: Any,
) -> dict[str, tuple[str, str]]:
    """Build a role->worker_type mapping for Windows pools by querying TC.

    Fetches all worker type names for the provisioner, filters to those
    starting with "win", and derives the role key by removing dashes.

    Args:
        provisioner: The provisioner ID (e.g., "releng-hardware")
        credentials: TaskCluster credentials

    Returns:
        Dict mapping role string (dashes removed) to (provisioner, pool_name)
    """
    pool_names = fetch_worker_type_names(provisioner, credentials)
    return {
        pool.replace("-", ""): (provisioner, pool) for pool in pool_names if pool.startswith("win")
    }


def match_workers_to_hosts(
    hosts: list[str],
    *,
    host_to_worker_type: dict[str, tuple[str, str]],
    worker_type_to_workers: dict[tuple[str, str], dict[Any, Any]],
    ts: str,
    observed_identities: dict[str, dict[str, str] | None] | None = None,
) -> list[dict[str, Any]]:
    """Match TaskCluster worker data to hosts and build worker records.

    Args:
        hosts: List of hostnames to match
        host_to_worker_type: Mapping of hostname to (provisioner, worker_type),
            resolved from observed identity, or legacy role/override metadata
        worker_type_to_workers: Mapping of (provisioner, worker_type) to worker data
        ts: Timestamp for the records

    Returns:
        List of worker record dictionaries ready to insert into SQLite
    """
    records = []

    for host in hosts:
        worker_type_key = host_to_worker_type.get(host)
        if not worker_type_key:
            continue

        provisioner, worker_type = worker_type_key
        workers_map = worker_type_to_workers.get(worker_type_key, {})

        # Match full observed identity; legacy observations use an unambiguous short ID.
        identity = (observed_identities or {}).get(host)
        short_host = identity["workerId"] if identity else strip_fqdn(host)
        worker_key = (identity["workerGroup"], short_host) if identity else short_host
        worker_data = workers_map.get(worker_key)

        if worker_data:
            # Extract fields from the Worker Manager REST response.
            state = worker_data.get("state")
            last_date_active = worker_data.get("lastDateActive")
            quarantine_until = worker_data.get("quarantineUntil")

            # Queue status enrichment is nested under latestTask.run.
            task_id = None
            task_run_id = None
            task_started = None
            task_resolved = None
            task_state = None
            latest_task = worker_data.get("latestTask")
            if latest_task:
                if latest_task.get("taskId") and not latest_task.get("run"):
                    # Task status lookup failed: preserve the last successful observation.
                    continue
                task_id = latest_task.get("taskId")
                task_run_id = latest_task.get("runId")
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
                "worker_group": worker_data.get("workerGroup"),
                "provisioner": provisioner,
                "worker_type": worker_type,
                "state": state,
                "last_date_active": last_date_active,
                "task_id": task_id,
                "task_run_id": task_run_id,
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
    use_historical = getattr(args, "use_historical_identity", False)

    # Track warnings for quiet mode
    api_errors = 0
    legacy_hosts = 0
    pool_mismatches = 0
    uncertain_historical = 0
    ambiguous_hosts = 0
    unresolved_hosts = 0

    # Load credentials
    credentials = load_tc_credentials()
    clients = create_tc_clients(credentials)

    # Parse host list
    hosts = []
    if is_host_file(args.host):
        hosts = parse_host_list(Path(args.host))
    else:
        hosts = [args.host]

    if not quiet:
        click.echo(f"Fetching TaskCluster data for {len(hosts)} host(s)...")

    # Initialize SQLite database
    from ..db import (
        get_connection,
        get_db_path,
        get_latest_tc_workers,
        init_db,
        insert_tc_lookup_attempt,
        insert_tc_worker,
    )

    db_path = get_db_path()
    init_db(db_path)
    db_conn = get_connection(db_path)

    try:
        # Map hosts to roles from SQLite
        host_to_role = get_host_roles_bulk(set(hosts), db_conn)
        observed_identities = get_observed_identities(hosts, db_conn=db_conn)
        historical = {}
        if use_historical:
            from ..worker_history import IDENTITY_FIELDS, load_history, render_history
            from .monitor.cache import ShaInfoCache

            overrides = db_path.parent / OVERRIDES_DIR_NAME
            cache = ShaInfoCache(overrides, db_path.parent / "vault_yamls")
            states = load_history(db_conn, hosts=hosts)
            for host in hosts:
                baseline = render_history(states.get(host), sha_cache=cache, now=datetime.now(UTC))
                historical[host] = baseline
                observed_identities[host] = (
                    {field: baseline[key] for key, field in IDENTITY_FIELDS.items()}
                    if baseline["status"] == "present"
                    else None
                )
                if baseline["contradiction_status"] == "contradicted":
                    pool_mismatches += 1
                elif baseline["contradiction_status"] != "none_known":
                    uncertain_historical += 1
                if verbose and not quiet:
                    click.echo(
                        f"{host}: historical identity from {baseline['observation_ts']}; "
                        f"contradiction={baseline['contradiction_status']} (diagnostic evidence only)"
                    )

        # Track hosts without roles for logging
        for host, role in host_to_role.items():
            if role:
                if verbose >= 1 and not quiet:
                    click.echo(f"  {host} -> role: {role}")
            elif verbose >= 1 and not quiet:
                click.echo(f"  {host} -> role: NOT FOUND")

        # Build role to hosts mapping
        role_to_hosts = build_role_to_hosts_mapping(host_to_role)

        # Extend role lookup with dynamically-fetched Windows pool mapping if needed
        role_lookup = ROLE_TO_TASKCLUSTER
        windows_roles = [
            role
            for host, role in host_to_role.items()
            if host not in observed_identities
            and role
            and role.startswith("win")
            and "_" not in role
        ]
        if windows_roles:
            if not quiet:
                click.echo("Fetching Windows worker pool names from TaskCluster...")
            try:
                windows_mapping = build_windows_role_mapping("releng-hardware", credentials)
                role_lookup = {**ROLE_TO_TASKCLUSTER, **windows_mapping}
            except FleetRollError as exc:
                api_errors += 1
                if verbose and not quiet:
                    click.echo(f"ERROR: Windows worker pool discovery failed: {exc}", err=True)

        # Map roles to workerTypes
        role_to_worker_type, _, _ = map_roles_to_worker_types(role_to_hosts, role_lookup)

        # Apply per-host WORKER_TYPE_OVERRIDE on top of the role-based mapping.
        # A deployed override file can move a host to a different worker pool
        # (e.g. gecko-t-linux-talos-2404 -> gecko-t-linux-talos-2404-bug2031822).
        overrides_dir = default_audit_log_path().parent / OVERRIDES_DIR_NAME
        host_to_override_sha = get_host_override_shas_bulk(set(hosts), db_conn)
        host_to_override_pool = build_host_override_pools(host_to_override_sha, overrides_dir)
        host_to_worker_type = resolve_host_worker_types(
            hosts,
            host_to_role=host_to_role,
            role_to_worker_type=role_to_worker_type,
            host_to_override_pool=host_to_override_pool,
            observed_identities=observed_identities,
        )
        worker_type_to_hosts = invert_host_worker_types(host_to_worker_type)
        task_run_cache = build_task_run_cache(get_latest_tc_workers(db_conn, hosts))

        if verbose >= 1 and not quiet:
            for host, pool in host_to_override_pool.items():
                if pool:
                    click.echo(f"  {host} -> worker pool override: {pool}")

        for host in hosts:
            identity = observed_identities.get(host)
            if identity:
                intended_pool = host_to_override_pool.get(host)
                if intended_pool and intended_pool != identity["workerType"]:
                    pool_mismatches += 1
                    if verbose and not quiet:
                        click.echo(
                            f"WARNING: {host}: observed pool {identity['workerType']} differs from "
                            f"WORKER_TYPE_OVERRIDE={intended_pool}; querying observed identity",
                            err=True,
                        )
            elif host in host_to_worker_type:
                legacy_hosts += 1
                if verbose and not quiet:
                    click.echo(
                        f"WARNING: {host}: using legacy role/override pool resolution; "
                        "refresh with gather host to collect worker identity",
                        err=True,
                    )
            else:
                unresolved_hosts += 1
                if verbose and not quiet:
                    click.echo(
                        f"ERROR: {host}: worker identity unresolved; refresh with gather host "
                        "and check worker configuration",
                        err=True,
                    )

        # Show summary of what we're fetching
        if not quiet:
            role_summary = ", ".join(
                f"{role} ({len(hosts)} host(s))"
                for role, hosts in role_to_hosts.items()
                if role in role_to_worker_type
            )
            if role_summary:
                click.echo(f"Found legacy roles: {role_summary}")

        # Fetch data for each workerType
        ts = utc_now_iso()
        worker_type_to_workers: dict[tuple[str, str], dict[Any, Any]] = {}
        workers_by_pool: dict[tuple[str, str], list[dict[str, Any]]] = {}
        failed_pools: set[tuple[str, str]] = set()
        task_status_errors = 0

        for provisioner, worker_type in worker_type_to_hosts:
            worker_type_key = (provisioner, worker_type)
            if not quiet:
                click.echo(f"Querying workerType {provisioner}/{worker_type}...", nl=False)
            try:
                fetch_result = fetch_workers(
                    provisioner,
                    worker_type=worker_type,
                    clients=clients,
                    worker_ids={
                        strip_fqdn(host)
                        for host in worker_type_to_hosts[worker_type_key]
                        if host not in observed_identities
                    },
                    worker_identities={
                        (identity["workerGroup"], identity["workerId"])
                        for host in worker_type_to_hosts[worker_type_key]
                        if (identity := observed_identities.get(host))
                    },
                    cached_task_runs=task_run_cache,
                    verbose=(verbose >= 2 and not quiet),
                )
                workers_list = fetch_result.workers
                workers_by_pool[worker_type_key] = workers_list
                task_status_errors += fetch_result.status_errors

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
                workers_map = index_workers(workers_list)

                worker_type_to_workers[(provisioner, worker_type)] = workers_map
                if not quiet:
                    status_warning = (
                        f", {fetch_result.status_errors} task status error(s)"
                        if fetch_result.status_errors
                        else ""
                    )
                    click.echo(f" {len(workers_list)} worker(s) found{status_warning}")

            except FleetRollError as e:
                api_errors += 1
                failed_pools.add(worker_type_key)
                if not quiet:
                    click.echo(f" FAILED: {e}", err=True)
                # Store empty result so we still write scan record
                worker_type_to_workers[(provisioner, worker_type)] = {}

        worker_records_written = 0
        scan_records_written = 0

        # Match workers to hosts
        worker_records = match_workers_to_hosts(
            hosts,
            host_to_worker_type=host_to_worker_type,
            worker_type_to_workers=worker_type_to_workers,
            ts=ts,
            observed_identities=observed_identities,
        )

        # Write worker records to SQLite
        for record in worker_records:
            host = record["host"]
            if use_historical:
                record["resolution_source"] = "historical_worker_identity"
                record["historical_baseline"] = historical[host]["provenance"]
            short_host = record["worker_id"]

            if verbose >= 1 and not quiet:
                provisioner = record["provisioner"]
                worker_type = record["worker_type"]
                worker_type_key = (provisioner, worker_type)
                workers_map = worker_type_to_workers.get(worker_type_key, {})

                click.echo(f"  Matching {host} (worker ID: {short_host})...")
                click.echo(f"    Found worker data for {short_host}")
                if verbose >= 2:
                    worker_data = workers_map.get((record["worker_group"], short_host))
                    click.echo(f"    Worker data: {json.dumps(worker_data, indent=4)}")

            # Insert into SQLite
            insert_tc_worker(db_conn, record)
            worker_records_written += 1

            if verbose >= 1 and not quiet:
                state = record["state"]
                quarantine = record["quarantine_until"]
                click.echo(f"    Wrote record: state={state}, quarantine={quarantine}")

        # Persist successes and completed attempts in the same transaction. Failures
        # advance attempt evidence without changing the last successful observation.
        for attempt in build_lookup_attempts(
            hosts,
            host_to_worker_type=host_to_worker_type,
            observed_identities=observed_identities,
            host_to_override_pool=host_to_override_pool,
            workers_by_pool=workers_by_pool,
            failed_pools=failed_pools,
            worker_records=worker_records,
            ts=utc_now_iso(),
            started_ts=ts,
        ):
            if use_historical:
                attempt["resolution_source"] = "historical_worker_identity"
                attempt["historical_baseline"] = historical[attempt["host"]]["provenance"]
            insert_tc_lookup_attempt(db_conn, attempt)
            if attempt["status"] == "worker_ambiguous":
                ambiguous_hosts += 1
                if verbose and not quiet:
                    click.echo(
                        f"WARNING: {attempt['host']}: multiple workers match "
                        f"{attempt['identity']['worker_id']}; skipping ambiguous match",
                        err=True,
                    )

        # Commit all inserts
        db_conn.commit()
        written_hosts = {record["host"] for record in worker_records}
        failed_hosts = sorted(set(hosts) - written_hosts)
        for host in failed_hosts if verbose and not quiet else []:
            if host in host_to_worker_type:
                click.echo(
                    f"ERROR: {host}: no complete worker observation collected "
                    "(worker missing, ambiguous identity, or API failure)",
                    err=True,
                )

        # Log skipped hosts
        if verbose >= 1 and not quiet:
            written_hosts = {r["host"] for r in worker_records}
            for host in hosts:
                if host not in written_hosts:
                    if host not in host_to_worker_type:
                        click.echo(f"  Skipping {host}: no role or role not in worker type mapping")
                    else:
                        click.echo(f"  Skipping {host}: no worker data found")

        # Count scan records for quiet-mode output compatibility
        # (scan records are not written to SQLite as they're never read)
        scan_records_written = len(worker_type_to_hosts)

        # Build warnings list for quiet mode
        elapsed_seconds = time.time() - start_time
        failed = bool(failed_hosts or api_errors or task_status_errors)
        resolution_warnings = []
        if unresolved_hosts:
            resolution_warnings.append(f"{unresolved_hosts} unresolved identities")
        if legacy_hosts:
            resolution_warnings.append(f"{legacy_hosts} legacy fallback(s)")
        if pool_mismatches:
            resolution_warnings.append(f"{pool_mismatches} pool mismatch(es)")
        if ambiguous_hosts:
            resolution_warnings.append(f"{ambiguous_hosts} ambiguous worker match(es)")
        if uncertain_historical:
            resolution_warnings.append(f"{uncertain_historical} uncertain historical identit(ies)")
        if quiet:
            output = format_tc_fetch_quiet(
                worker_count=worker_records_written,
                host_count=len(hosts),
                warnings=bool(resolution_warnings),
                elapsed_seconds=elapsed_seconds,
                failed=failed,
            )
            click.echo(output)
        else:
            if resolution_warnings:
                click.echo(
                    "WARNING: " + ", ".join(resolution_warnings) + "; gather-tc -v for details"
                )
            msg = (
                f"Wrote {worker_records_written} worker record(s) and "
                f"{scan_records_written} scan record(s) to {db_path}"
            )
            click.echo(msg)

        if failed:
            if quiet:
                raise CommandFailureError(rc=2)
            raise FleetRollError(
                f"Taskcluster collection incomplete: {len(failed_hosts)} host(s) not collected, "
                f"{api_errors} pool API error(s), {task_status_errors} task status error(s); "
                "gather-tc -v for details"
            )

        # Also refresh GitHub branch refs (throttled)
        from ..github import do_github_fetch

        do_github_fetch(quiet=quiet)
    finally:
        db_conn.close()
