"""Data freshness reporting command."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..cli_types import DataFreshnessArgs


def cmd_data_freshness(args: DataFreshnessArgs) -> None:
    """Report data freshness status and exit non-zero if stale or no data."""
    from ..constants import STALE_DATA_THRESHOLD_SECONDS
    from ..db import get_connection, get_db_path, init_db
    from .monitor.data import age_seconds, humanize_duration, most_recent_ok_ts

    threshold = (
        args.stale_threshold if args.stale_threshold is not None else STALE_DATA_THRESHOLD_SECONDS
    )
    min_fresh_pct = args.min_fresh_pct

    db_path = get_db_path()
    if not db_path.exists():
        _output(
            {
                "status": "no_data",
                "most_recent_ok_ts": None,
                "ok_age_seconds": None,
                "ok_age_human": None,
                "hosts_total": 0,
                "hosts_with_ok": 0,
                "hosts_fresh": 0,
                "fresh_pct": 0.0,
                "min_fresh_pct": min_fresh_pct,
                "stale_threshold_seconds": threshold,
            },
            json_output=args.json,
        )
        sys.exit(1)

    init_db(db_path)
    conn = get_connection(db_path)

    if args.hosts_file:
        from pathlib import Path

        from ..utils import parse_host_list

        hosts = parse_host_list(Path(args.hosts_file))
    else:
        rows = conn.execute("SELECT DISTINCT host FROM host_observations").fetchall()
        hosts = [r["host"] for r in rows]

    from ..db import get_latest_host_observations

    _latest, latest_ok = get_latest_host_observations(conn, hosts)
    hosts_total = len(hosts)
    hosts_with_ok = len(latest_ok)
    most_recent_ok = most_recent_ok_ts(latest_ok)

    hosts_fresh = sum(
        1
        for rec in latest_ok.values()
        if (age := age_seconds(rec["ts"])) is not None and age <= threshold
    )
    fresh_pct = round(100.0 * hosts_fresh / hosts_total, 1) if hosts_total > 0 else 0.0

    ok_age = age_seconds(most_recent_ok) if most_recent_ok else None

    if hosts_total == 0 or hosts_with_ok == 0:
        status = "no_data"
    elif fresh_pct < min_fresh_pct:
        status = "stale"
    else:
        status = "fresh"

    _output(
        {
            "status": status,
            "most_recent_ok_ts": most_recent_ok,
            "ok_age_seconds": ok_age,
            "ok_age_human": humanize_duration(ok_age) if ok_age is not None else None,
            "hosts_total": hosts_total,
            "hosts_with_ok": hosts_with_ok,
            "hosts_fresh": hosts_fresh,
            "fresh_pct": fresh_pct,
            "min_fresh_pct": min_fresh_pct,
            "stale_threshold_seconds": threshold,
        },
        json_output=args.json,
    )

    if status != "fresh":
        sys.exit(1)


def _output(result: dict, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result))
        return
    status = result["status"]
    ok_ts = result["most_recent_ok_ts"] or "none"
    ok_age = result["ok_age_human"] or "-"
    hosts_total = result["hosts_total"]
    hosts_with_ok = result["hosts_with_ok"]
    hosts_fresh = result["hosts_fresh"]
    fresh_pct = result["fresh_pct"]
    min_fresh_pct = result["min_fresh_pct"]
    threshold = result["stale_threshold_seconds"]
    print(f"status:          {status}")
    print(f"most_recent_ok:  {ok_ts} ({ok_age} ago)")
    print(f"hosts_with_ok:   {hosts_with_ok}/{hosts_total}")
    print(f"fresh:           {hosts_fresh}/{hosts_total} ({fresh_pct}%, min {min_fresh_pct}%)")
    print(f"stale_threshold: {threshold}s")
