"""Report recent Taskcluster outcomes for hosts with deployed overrides.

The report compares each worker's recent task runs with the override deployment
time recorded in Fleetroll's audit log. It uses the most recent successful host
observation from Fleetroll's local database to determine the active override and
worker type.
"""

# ruff: noqa: INP001
# This is a standalone operator script, not a package module.

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from fleetroll.commands.gather_tc import build_host_override_pools, resolve_host_worker_types
from fleetroll.commands.monitor.cache import parse_override_file
from fleetroll.commands.monitor.data import strip_fqdn
from fleetroll.constants import (
    DEFAULT_TC_PROVISIONER,
    OVERRIDES_DIR_NAME,
    ROLE_TO_TASKCLUSTER,
)
from fleetroll.db import get_connection, get_db_path, get_latest_host_observations
from fleetroll.taskcluster import create_tc_clients, load_tc_credentials
from fleetroll.utils import default_audit_log_path, is_host_file, parse_host_list

OUTCOMES = {"completed", "failed", "exception", "running", "pending"}
RECENT_TASK_LIMIT = 20


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "hosts",
        nargs="*",
        help="Hostnames or one host-list file; omit only when using --all-overrides.",
    )
    parser.add_argument(
        "--all-overrides",
        action="store_true",
        help="Check every host with an override in the latest successful Fleetroll observations.",
    )
    parser.add_argument(
        "--worker-group",
        default="mdc1",
        help="Taskcluster worker group (default: mdc1).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=get_db_path(),
        help="Fleetroll SQLite database path.",
    )
    parser.add_argument(
        "--audit-log",
        type=Path,
        default=default_audit_log_path(),
        help="Fleetroll JSONL audit log path.",
    )
    return parser.parse_args()


def parse_requested_hosts(host_args: list[str], *, all_overrides: bool) -> list[str] | None:
    """Return requested hostnames, or None when all hosts are requested."""
    if all_overrides:
        if host_args:
            raise ValueError("Pass hostnames or --all-overrides, not both.")
        return None
    if len(host_args) == 1 and is_host_file(host_args[0]):
        return parse_host_list(Path(host_args[0]))
    if not host_args:
        raise ValueError("Pass hostnames, a host-list file, or --all-overrides.")
    return list(dict.fromkeys(host_args))


def load_latest_successful_observations(
    conn: sqlite3.Connection,
) -> dict[str, dict[str, Any]]:
    """Load each host's most recent successful observation."""
    hosts = [row[0] for row in conn.execute("SELECT DISTINCT host FROM host_observations")]
    observations: dict[str, dict[str, Any]] = {}
    for start in range(0, len(hosts), 500):
        _, latest_ok = get_latest_host_observations(conn, hosts[start : start + 500])
        observations.update(latest_ok)
    return observations


def resolve_requested_hosts(
    requested: list[str] | None,
    observations: dict[str, dict[str, Any]],
) -> tuple[dict[str, str], list[str]]:
    """Map input names to recorded hostnames and return lookup errors."""
    if requested is None:
        return {
            host: host
            for host, record in observations.items()
            if (record.get("observed") or {}).get("override_present")
        }, []

    resolved: dict[str, str] = {}
    errors: list[str] = []
    for name in requested:
        if name in observations:
            resolved[name] = name
            continue
        matches = [host for host in observations if strip_fqdn(host) == strip_fqdn(name)]
        if len(matches) == 1:
            resolved[name] = matches[0]
        elif not matches:
            errors.append(f"{name}: no successful host observation in Fleetroll database")
        else:
            errors.append(f"{name}: short hostname matches multiple recorded hosts")
    return resolved, errors


def parse_timestamp(value: str | None) -> dt.datetime | None:
    """Parse an ISO 8601 timestamp as UTC."""
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def load_deployment_events(path: Path) -> dict[tuple[str, str], dt.datetime]:
    """Load successful override writes keyed by short hostname and SHA."""
    events: dict[tuple[str, str], dt.datetime] = {}
    if not path.exists():
        return events
    with path.open(encoding="utf-8") as audit_file:
        for line in audit_file:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("action") != "host.set_override" or not record.get("ok"):
                continue
            parameters = record.get("parameters") or {}
            sha = parameters.get("sha256")
            timestamp = parse_timestamp(record.get("ts"))
            host = record.get("host")
            if not (isinstance(host, str) and isinstance(sha, str) and timestamp):
                continue
            key = (strip_fqdn(host), sha)
            events[key] = max(timestamp, events.get(key, timestamp))
    return events


def deployment_time(
    host: str,
    observation: dict[str, Any],
    deployment_events: dict[tuple[str, str], dt.datetime],
) -> tuple[dt.datetime | None, str | None]:
    """Get the current override's deployment time, preferring Fleetroll audit."""
    observed = observation.get("observed") or {}
    sha = observed.get("override_sha256")
    if not observed.get("override_present") or not isinstance(sha, str):
        return None, sha if isinstance(sha, str) else None

    event_time = deployment_events.get((strip_fqdn(host), sha))
    if event_time:
        return event_time, sha

    meta = observed.get("override_meta") or {}
    mtime = meta.get("mtime_epoch")
    try:
        if mtime is not None:
            return dt.datetime.fromtimestamp(float(mtime), tz=dt.UTC), sha
    except (TypeError, ValueError, OSError):
        pass
    return None, sha


def worker_type_for_host(
    host: str,
    observation: dict[str, Any],
    overrides_dir: Path,
) -> tuple[str, str] | None:
    """Resolve the Taskcluster pool from the host role and deployed override."""
    observed = observation.get("observed") or {}
    sha = observed.get("override_sha256")
    role = observed.get("role")
    host_to_override_pool = build_host_override_pools(
        {host: sha if isinstance(sha, str) else None}, overrides_dir
    )
    if sha and not (overrides_dir / sha[:12]).exists():
        raise FileNotFoundError(f"stored override content for SHA {sha[:12]} is missing")
    if sha:
        content = overrides_dir / sha[:12]
        if content.exists() and not parse_override_file(content):
            raise ValueError(f"stored override content for SHA {sha[:12]} is invalid")
    return resolve_host_worker_types(
        [host],
        host_to_role={host: role if isinstance(role, str) else None},
        role_to_worker_type=ROLE_TO_TASKCLUSTER,
        host_to_override_pool=host_to_override_pool,
        default_provisioner=DEFAULT_TC_PROVISIONER,
    ).get(host)


def recent_task_run(
    clients: Any,
    *,
    deployed_at: dt.datetime,
    task_ref: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a recent task run when it started at or after deployment."""
    task_id = task_ref.get("taskId")
    run_id = task_ref.get("runId")
    if not isinstance(task_id, str) or not isinstance(run_id, int):
        return None
    status = clients.queue.status(task_id).get("status") or {}
    run = next((item for item in status.get("runs", []) if item.get("runId") == run_id), None)
    if not run:
        return None
    started = parse_timestamp(run.get("started"))
    if not started or started < deployed_at:
        return None

    outcome = run.get("state") or status.get("state") or run.get("reasonResolved") or "unknown"
    name = None
    if outcome in {"failed", "exception", "unknown"}:
        task = clients.queue.task(task_id)
        name = (task.get("metadata") or {}).get("name")
    return {
        "task_id": task_id,
        "run_id": run_id,
        "started": run.get("started"),
        "resolved": run.get("resolved"),
        "outcome": outcome if outcome in OUTCOMES else "unknown",
        "name": name,
    }


def collect_host_report(
    clients: Any,
    *,
    input_name: str,
    host: str,
    observation: dict[str, Any],
    deployed_at: dt.datetime,
    sha: str,
    override_branch: str,
    worker_type: tuple[str, str],
    worker_group: str,
) -> dict[str, Any]:
    """Fetch recent worker task runs and summarize their outcomes."""
    provisioner, pool = worker_type
    short_host = strip_fqdn(host)
    report: dict[str, Any] = {
        "host": input_name,
        "recorded_host": host,
        "observation_ts": observation.get("ts"),
        "override_sha256": sha,
        "override_branch": override_branch,
        "deployed_at": deployed_at.isoformat().replace("+00:00", "Z"),
        "worker_type": pool,
        "worker_group": worker_group,
        "history_window": 0,
        "post_deploy_jobs": 0,
        "outcomes": dict.fromkeys(sorted(OUTCOMES), 0),
        "jobs": [],
        "error": None,
    }
    worker = clients.worker_manager.getWorker(provisioner, pool, worker_group, short_host)
    task_refs = worker.get("recentTasks") or []
    report["history_window"] = len(task_refs)
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(20, max(1, len(task_refs)))
    ) as pool_executor:
        futures = [
            pool_executor.submit(
                recent_task_run,
                clients,
                deployed_at=deployed_at,
                task_ref=task_ref,
            )
            for task_ref in task_refs
        ]
        for future in concurrent.futures.as_completed(futures):
            task = future.result()
            if task:
                report["jobs"].append(task)

    report["jobs"].sort(key=lambda job: job.get("started") or "")
    report["post_deploy_jobs"] = len(report["jobs"])
    for task in report["jobs"]:
        outcome = task["outcome"]
        report["outcomes"][outcome] = report["outcomes"].get(outcome, 0) + 1
    report["history_may_be_truncated"] = (
        len(task_refs) >= RECENT_TASK_LIMIT and report["post_deploy_jobs"] >= RECENT_TASK_LIMIT
    )
    return report


def print_human_report(reports: list[dict[str, Any]], errors: list[str]) -> None:
    """Print host summaries grouped by override, followed by any issues."""
    grouped_reports: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for report in reports:
        key = (report["override_branch"], report["override_sha256"])
        grouped_reports.setdefault(key, []).append(report)

    issues: list[tuple[str, str, dict[str, Any]]] = []
    for branch, sha in sorted(grouped_reports):
        group_reports = sorted(grouped_reports[(branch, sha)], key=lambda item: item["host"])
        print(f"Override: {branch} ({sha[:8]}) — {len(group_reports)} host(s)")
        print(
            f"{'HOST':<24} {'WORKER TYPE':<36} {'DEPLOYED UTC':<17} {'JOBS':>4} "
            f"{'COMPLETED':>9} {'FAILED':>6} {'EXCEPTION':>9} {'RUNNING':>7} {'OTHER':>5}"
        )
        for report in group_reports:
            outcomes = report["outcomes"]
            other = outcomes.get("pending", 0) + outcomes.get("unknown", 0)
            host = strip_fqdn(report["host"])
            pool = report["worker_type"]
            timestamp = parse_timestamp(report["deployed_at"])
            deployed_at = timestamp.strftime("%Y-%m-%d %H:%MZ") if timestamp else "unknown"
            print(
                f"{host:<24} {pool:<36} {deployed_at:<17} {report['post_deploy_jobs']:>4} "
                f"{outcomes.get('completed', 0):>9} {outcomes.get('failed', 0):>6} "
                f"{outcomes.get('exception', 0):>9} {outcomes.get('running', 0):>7} {other:>5}"
            )
            if report.get("history_may_be_truncated"):
                print("  Recent-task window is full; earlier post-deployment jobs may be omitted.")
            if report["post_deploy_jobs"] == 0:
                print("  No post-deployment tasks in the worker's recent task history.")
            issues.extend(
                (task["outcome"], report["host"], task)
                for task in report["jobs"]
                if task["outcome"] in {"failed", "exception", "unknown"}
            )

    if issues or errors:
        print("\nIssues")
        for outcome, host, task in sorted(issues, key=lambda item: (item[0], item[1])):
            task_name = task.get("name") or task["task_id"]
            print(f"  {outcome.upper()}: {strip_fqdn(host)} — {task_name}")
            started = task.get("started") or "unknown"
            resolved = task.get("resolved") or "—"
            print(f"    Started {started}; resolved {resolved}")
            print(f"    https://firefox-ci-tc.services.mozilla.com/tasks/{task['task_id']}")
    for error in errors:
        print(f"  ERROR: {error}")


def main() -> int:
    """Run the recent post-deployment Taskcluster health report."""
    args = parse_args()
    try:
        requested = parse_requested_hosts(args.hosts, all_overrides=args.all_overrides)
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if not args.db.exists():
        print(
            f"ERROR: Fleetroll database not found: {args.db}; run gather-host first.",
            file=sys.stderr,
        )
        return 2

    conn = get_connection(args.db)
    try:
        observations = load_latest_successful_observations(conn)
    finally:
        conn.close()

    resolved_hosts, errors = resolve_requested_hosts(requested, observations)
    if args.all_overrides:
        resolved_hosts = {
            host: host
            for host, record in observations.items()
            if (record.get("observed") or {}).get("override_present")
        }
    if not resolved_hosts and not errors:
        errors.append("no hosts with overrides found in successful Fleetroll observations")

    deployment_events = load_deployment_events(args.audit_log)
    overrides_dir = default_audit_log_path().parent / OVERRIDES_DIR_NAME
    clients = None
    reports: list[dict[str, Any]] = []

    for input_name, host in resolved_hosts.items():
        observation = observations[host]
        deployed_at, sha = deployment_time(host, observation, deployment_events)
        if not sha:
            errors.append(f"{input_name}: no override SHA in the latest successful observation")
            continue
        if not deployed_at:
            errors.append(f"{input_name}: cannot determine the override deployment time")
            continue
        try:
            worker_type = worker_type_for_host(host, observation, overrides_dir)
        except (FileNotFoundError, ValueError) as exc:
            errors.append(f"{input_name}: {exc}")
            continue
        if not worker_type:
            errors.append(f"{input_name}: no Taskcluster worker type mapping for the recorded role")
            continue
        if not clients:
            try:
                clients = create_tc_clients(load_tc_credentials())
            except Exception as exc:
                errors.append(f"could not create Taskcluster client: {exc}")
                break
        try:
            override_info = parse_override_file(overrides_dir / sha[:12]) or {}
            override_branch = override_info.get("branch") or "unknown branch"
            reports.append(
                collect_host_report(
                    clients,
                    input_name=input_name,
                    host=host,
                    observation=observation,
                    deployed_at=deployed_at,
                    sha=sha,
                    override_branch=override_branch,
                    worker_type=worker_type,
                    worker_group=args.worker_group,
                )
            )
        except Exception as exc:
            errors.append(f"{input_name}: Taskcluster query failed: {exc}")

    if args.json:
        print(json.dumps({"hosts": reports, "errors": errors}, indent=2))
    else:
        print_human_report(reports, errors)

    has_bad_outcome = any(
        report["outcomes"].get(outcome, 0)
        for report in reports
        for outcome in ("failed", "exception", "unknown")
    )
    return 1 if errors or has_bad_outcome else 0


if __name__ == "__main__":
    raise SystemExit(main())
