"""Summarize recorded override and Puppet state for a fleet population."""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import click

from ..audit import iter_audit_records
from ..db import get_connection, get_db_path, get_latest_host_observations
from ..queue_counts import collect_queue_counts, render_queue_counts
from ..rollout_history import (
    WRITE_ACTIONS,
    applied_time,
    override_info,
    parse_timestamp,
    worker_identity,
)
from ..utils import default_audit_log_path, parse_host_list


def build_report(
    *,
    hosts: list[str],
    latest: dict,
    successful: dict,
    writes: dict,
    role: str | None,
    worker_type: str | None,
    overrides_dir: Path,
    scoped: bool,
) -> dict[str, Any]:
    """Build the report from recorded evidence without refreshing hosts."""
    groups: dict[str | None, dict[str, Any]] = {}
    unresolved, nonmatching, missing = [], [], []
    failed, pending, legacy, timestamps = [], [], [], []
    for host in sorted(set(hosts)):
        record = successful.get(host)
        if not record:
            missing.append(host)
            unresolved.append(host)
            continue
        observed = record.get("observed") or {}
        if role:
            membership = observed.get("role")
            match = membership == role if membership else None
        elif worker_type:
            identity, _ = worker_identity(record, overrides_dir=overrides_dir)
            match = f"{identity.provisioner}/{identity.pool}" == worker_type if identity else None
            if match and identity and identity.legacy:
                legacy.append(host)
        else:
            match = True
        if match is None:
            unresolved.append(host)
            continue
        if not match:
            nonmatching.append(host)
            continue
        stamp = parse_timestamp(record.get("ts"))
        timestamps.append(record.get("ts"))
        newer = parse_timestamp((latest.get(host) or {}).get("ts"))
        if latest.get(host) and not latest[host].get("ok") and newer and stamp and newer > stamp:
            failed.append(host)
        write = writes.get(host.split(".")[0].lower())
        changed = bool(write and (not stamp or write > stamp))
        if changed:
            pending.append(host)
        present = observed.get("override_present")
        sha = observed.get("override_sha256") if present is True else None
        key = (
            sha
            if present is True and isinstance(sha, str) and sha
            else None
            if present is False
            else "unknown"
        )
        if key not in groups:
            info = override_info(key, overrides_dir) if key and key != "unknown" else None
            groups[key] = {
                "override_sha256": key if key != "unknown" else None,
                "state": "unknown" if key == "unknown" else "present" if key else "absent",
                "branch": info.get("branch") if info else None,
                "hosts": [],
                "puppet_status": Counter(),
            }
        group = groups[key]
        group["hosts"].append(host)
        success = observed.get("puppet_success")
        applied = observed.get("puppet_override_sha_applied")
        puppet_time = applied_time(observed)
        if (
            changed
            or not puppet_time
            or (write and puppet_time <= write)
            or success is None
            or "puppet_override_sha_applied" not in observed
            or key == "unknown"
        ):
            status = "unknown"
        elif success is False:
            status = "failed"
        elif success is not True:
            status = "unknown"
        elif applied == (sha if present else None):
            status = "applied"
        else:
            status = "mismatch"
        group["puppet_status"][status] += 1
    total = sum(len(group["hosts"]) for group in groups.values())
    for group in groups.values():
        group["count"] = len(group["hosts"])
        group["percentage"] = round(100 * group["count"] / total, 1) if total else None
        group["puppet_status"] = {
            k: group["puppet_status"][k] for k in ("applied", "mismatch", "failed", "unknown")
        }
    totals = {}
    for state in ("present", "absent", "unknown"):
        count = sum(group["count"] for group in groups.values() if group["state"] == state)
        totals[state] = {
            "count": count,
            "percentage": round(100 * count / total, 1) if total else None,
        }
    return {
        "schema_version": 1,
        "selector": {"role": role, "worker_type": worker_type},
        "population_source": "host_list" if scoped else "recorded_hosts",
        "matching_hosts": total,
        "override_totals": totals,
        "nonmatching_hosts": nonmatching,
        "unresolved_hosts": unresolved,
        "missing_successful_observations": missing,
        "newer_failed_gathers": failed,
        "unverified_writes": pending,
        "legacy_mapped_hosts": legacy,
        "observation_range": {
            "earliest": min((t for t in timestamps if t), default=None),
            "latest": max((t for t in timestamps if t), default=None),
        },
        "groups": sorted(groups.values(), key=lambda g: (g["state"], g["override_sha256"] or "")),
        "note": "Manual changes after observation may not be reflected.",
    }


def render_report(report: dict[str, Any], *, verbose: bool = False, color: bool = False) -> str:
    """Render a compact summary, with host lists available on request."""
    selector = report["selector"]
    scope = "Role" if selector["role"] else "Worker pool"
    lines = [f"{scope}: {selector['role'] or selector['worker_type']}"]
    total = report["matching_hosts"]
    source = "host list" if report["population_source"] == "host_list" else "recorded hosts"
    lines.append(f"Matching hosts: {total}  (source: {source})")
    if "queue_counts" in report:
        lines.extend(["", *render_queue_counts(report["queue_counts"], color=color)])
    lines.append("")
    lines.append("Overrides")
    for state, label in (("present", "Present"), ("absent", "Absent"), ("unknown", "Unknown")):
        data = report["override_totals"][state]
        percentage = f"{data['percentage']:.1f}%" if data["percentage"] is not None else "—"
        lines.append(f"  {label:<8} {data['count']:>5}  {percentage:>6}")
    for group in sorted(report["groups"], key=lambda item: (-item["count"], item["state"])):
        lines.append("")
        if group["state"] == "present":
            lines.append(f"  {group['branch'] or 'Unknown branch'}")
            lines.append(f"    Override SHA-256: {group['override_sha256']}")
        else:
            lines.append("  No override" if group["state"] == "absent" else "  Unknown override")
        percent = f"{group['percentage']:.1f}%" if group["percentage"] is not None else "—"
        lines.append(f"    Hosts: {group['count']} ({percent})")
        statuses = group["puppet_status"]
        parts = [
            f"{statuses[name]} {name}"
            for name in ("applied", "mismatch", "failed", "unknown")
            if statuses[name]
        ]
        lines.append(f"    Puppet: {', '.join(parts) if parts else 'no data'}")
        if verbose:
            lines.extend(f"      {host}" for host in group["hosts"])
    lines.extend(["", "Observation coverage"])
    interval = report["observation_range"]
    lines.append(
        f"  Matching hosts observed: {interval['earliest'] or 'unknown'} to {interval['latest'] or 'unknown'}"
    )
    lines.append(f"  Outside selection: {len(report['nonmatching_hosts'])}")
    lines.append(
        f"  Membership unresolved: {len(report['unresolved_hosts'])} "
        f"({len(report['missing_successful_observations'])} without a successful observation)"
    )
    for key, label in (
        ("newer_failed_gathers", "Newer failed gathers"),
        ("unverified_writes", "Writes awaiting verification"),
        ("legacy_mapped_hosts", "Legacy-mapped hosts"),
    ):
        if report[key]:
            lines.append(f"  {label}: {len(report[key])}")
            if not verbose and len(report[key]) <= 5:
                lines.extend(f"    {host}" for host in report[key])
    if verbose:
        for key, label in (
            ("nonmatching_hosts", "Outside selection"),
            ("unresolved_hosts", "Membership unresolved"),
            ("newer_failed_gathers", "Newer failed gathers"),
            ("unverified_writes", "Writes awaiting verification"),
            ("legacy_mapped_hosts", "Legacy-mapped hosts"),
        ):
            if report[key]:
                lines.append(f"  {label}:")
                lines.extend(f"    {host}" for host in report[key])
    else:
        lines.append("  Use --verbose or --json for host lists.")
    lines.extend(["", report["note"]])
    return "\n".join(lines)


def run_fleet_status(
    *,
    hosts_file: str | None,
    role: str | None,
    worker_type: str | None,
    json_output: bool,
    verbose: bool = False,
) -> None:
    path = get_db_path()
    if not path.exists():
        raise click.ClickException(f"Database not found: {path}")
    conn = get_connection(path)
    try:
        known = [row[0] for row in conn.execute("SELECT DISTINCT host FROM host_observations")]
        if hosts_file:
            hosts = []
            for host in parse_host_list(Path(hosts_file)):
                matches = [name for name in known if name.split(".")[0].lower() == host.lower()]
                hosts.append(matches[0] if host not in known and len(matches) == 1 else host)
        else:
            hosts = known
        latest, successful = get_latest_host_observations(conn, hosts)
    finally:
        conn.close()
    writes = {}
    for event in iter_audit_records(default_audit_log_path()):
        if not isinstance(event, dict) or event.get("action") not in WRITE_ACTIONS:
            continue
        host, stamp = event.get("host"), parse_timestamp(event.get("ts"))
        if isinstance(host, str) and stamp:
            key = host.split(".")[0].lower()
            if key not in writes or stamp > writes[key]:
                writes[key] = stamp
    report = build_report(
        hosts=hosts,
        latest=latest,
        successful=successful,
        writes=writes,
        role=role,
        worker_type=worker_type,
        overrides_dir=path.parent / "overrides",
        scoped=hosts_file is not None,
    )
    report["queue_counts"] = collect_queue_counts(
        report, observations=successful, overrides_dir=path.parent / "overrides"
    )
    if json_output:
        click.echo(json.dumps(report, indent=2, sort_keys=True))
        return
    click.echo(
        render_report(
            report,
            verbose=verbose,
            color=sys.stdout.isatty()
            and "NO_COLOR" not in os.environ
            and os.environ.get("TERM") != "dumb",
        )
    )
