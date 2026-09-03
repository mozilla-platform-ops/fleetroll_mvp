"""Data freshness reporting command."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3

    from ..cli_types import DataFreshnessArgs


def _source_result(
    records: dict[str, dict[str, Any]],
    *,
    hosts_total: int,
    threshold: int,
    min_fresh_pct: int,
    successful_only: bool,
) -> dict[str, Any]:
    """Summarize freshness for one source's latest per-host records."""
    from .monitor.data import age_seconds, humanize_duration, most_recent_ok_ts

    if successful_only:
        records = {host: record for host, record in records.items() if record.get("ok")}

    hosts_with_data = len(records)
    most_recent = most_recent_ok_ts(records)
    hosts_fresh = 0
    for record in records.values():
        timestamp = record.get("ts")
        record_age = age_seconds(timestamp) if isinstance(timestamp, str) else None
        if record_age is not None and record_age <= threshold:
            hosts_fresh += 1
    fresh_pct = round(100.0 * hosts_fresh / hosts_total, 1) if hosts_total else 0.0
    age = age_seconds(most_recent) if most_recent else None
    if hosts_total == 0 or hosts_with_data == 0:
        status = "no_data"
    elif fresh_pct < min_fresh_pct:
        status = "stale"
    else:
        status = "fresh"
    return {
        "status": status,
        "most_recent_data_ts": most_recent,
        "data_age_seconds": age,
        "data_age_human": humanize_duration(age) if age is not None else None,
        "hosts_with_data": hosts_with_data,
        "hosts_fresh": hosts_fresh,
        "fresh_pct": fresh_pct,
        "min_fresh_pct": min_fresh_pct,
        "stale_threshold_seconds": threshold,
    }


def _host_result(
    conn: sqlite3.Connection | None,
    hosts: list[str],
    *,
    threshold: int,
    min_fresh_pct: int,
) -> dict[str, Any]:
    if conn is None:
        latest_ok: dict[str, dict[str, Any]] = {}
    else:
        from ..db import get_latest_host_observations

        _latest, latest_ok = get_latest_host_observations(conn, hosts)
    return _source_result(
        latest_ok,
        hosts_total=len(hosts),
        threshold=threshold,
        min_fresh_pct=min_fresh_pct,
        successful_only=True,
    )


def _tc_result(
    conn: sqlite3.Connection | None,
    hosts: list[str],
    *,
    threshold: int,
    min_fresh_pct: int,
) -> dict[str, Any]:
    if conn is None:
        latest: dict[str, dict[str, Any]] = {}
    else:
        from ..db import get_latest_tc_workers

        latest = get_latest_tc_workers(conn, hosts)
    return _source_result(
        latest,
        hosts_total=len(hosts),
        threshold=threshold,
        min_fresh_pct=min_fresh_pct,
        successful_only=False,
    )


def _load_hosts(
    conn: sqlite3.Connection | None,
    *,
    hosts_file: str | None,
) -> tuple[str, list[str]]:
    if hosts_file:
        from ..utils import parse_host_list

        return hosts_file, parse_host_list(Path(hosts_file))
    if conn is None:
        return "--all", []

    hosts: set[str] = set()
    rows = conn.execute("SELECT DISTINCT host FROM host_observations").fetchall()
    hosts.update(row["host"] for row in rows)
    rows = conn.execute("SELECT DISTINCT host FROM tc_workers").fetchall()
    hosts.update(row["host"] for row in rows)
    return "--all", sorted(hosts)


def build_failures(source_results: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    """Explain why one or more freshness sources did not pass."""
    from .monitor.data import humanize_duration

    failures: list[dict[str, str]] = []
    labels = {"host": "host", "tc": "Taskcluster"}
    for name, result in source_results.items():
        if result["status"] == "fresh":
            continue

        label = labels.get(name, name)
        hosts_total = result["hosts_total"]
        hosts_with_data = result["hosts_with_data"]
        hosts_fresh = result["hosts_fresh"]
        threshold = result["stale_threshold_seconds"]
        age = result["data_age_seconds"]
        if hosts_total == 0:
            reason = "no hosts were selected"
        elif hosts_with_data == 0:
            reason = (
                f"no successful {label} observations are available; "
                f"freshness window is {humanize_duration(threshold)}"
            )
        elif hosts_fresh == 0 and isinstance(age, int) and age > threshold:
            reason = (
                f"most recent {label} observation is {humanize_duration(age)} old; "
                f"threshold is {humanize_duration(threshold)}"
            )
        else:
            reason = (
                f"fresh {label} coverage within {humanize_duration(threshold)} "
                f"is {result['fresh_pct']}% "
                f"({hosts_fresh}/{hosts_total}); minimum is {result['min_fresh_pct']}%"
            )
        failures.append({"source": name, "reason": reason})
    return failures


def cmd_data_freshness(args: DataFreshnessArgs) -> None:
    """Report freshness, optionally asserting that selected sources are fresh."""
    from ..constants import STALE_DATA_THRESHOLD_SECONDS
    from ..db import get_connection, get_db_path, init_db

    threshold = (
        args.stale_threshold if args.stale_threshold is not None else STALE_DATA_THRESHOLD_SECONDS
    )
    db_path = get_db_path()
    conn = None
    if db_path.exists():
        init_db(db_path)
        conn = get_connection(db_path)

    try:
        host_source, hosts = _load_hosts(conn, hosts_file=args.hosts_file)
        source_results = {
            "host": _host_result(
                conn,
                hosts,
                threshold=threshold,
                min_fresh_pct=args.min_fresh_pct,
            ),
            "tc": _tc_result(
                conn,
                hosts,
                threshold=threshold,
                min_fresh_pct=args.min_fresh_pct,
            ),
        }
    finally:
        if conn is not None:
            conn.close()

    for result in source_results.values():
        result["hosts_total"] = len(hosts)

    overall_status = (
        "fresh"
        if source_results and all(result["status"] == "fresh" for result in source_results.values())
        else "stale"
    )
    if source_results and all(result["status"] == "no_data" for result in source_results.values()):
        overall_status = "no_data"

    result = {
        "schema_version": 2,
        "status": overall_status,
        "host_source": host_source,
        "hosts_total": len(hosts),
        "required_sources": list(source_results),
        "sources": source_results,
        "failures": build_failures(source_results),
    }
    _output_sources(result, json_output=args.json)

    if args.require_fresh and overall_status != "fresh":
        sys.exit(1)


def _output_sources(result: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result))
        return
    print(f"status:      {result['status']}")
    print(f"host_source: {result['host_source']}")
    print(f"hosts_total: {result['hosts_total']}")
    print("source  status   most_recent_data  age  coverage")
    for name, source in result["sources"].items():
        timestamp = source["most_recent_data_ts"] or "none"
        age = source["data_age_human"] or "-"
        coverage = f"{source['hosts_fresh']}/{source['hosts_total']} ({source['fresh_pct']}%)"
        print(f"{name:<7} {source['status']:<8} {timestamp:<20} {age:<5} {coverage}")
    if result["failures"]:
        print("failures:")
        for failure in result["failures"]:
            print(f"  - {failure['source']}: {failure['reason']}")
