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
                "stale_threshold_seconds": threshold,
            },
            json_output=args.json,
        )
        sys.exit(1)

    init_db(db_path)
    conn = get_connection(db_path)

    if args.hosts_file:
        from pathlib import Path

        from ..db import get_latest_host_observations
        from ..utils import parse_host_list

        hosts = parse_host_list(Path(args.hosts_file))
        _latest, latest_ok = get_latest_host_observations(conn, hosts)
        hosts_total = len(hosts)
        hosts_with_ok = len(latest_ok)
        most_recent_ok = most_recent_ok_ts(latest_ok)
    else:
        row = conn.execute(
            "SELECT MAX(ts) AS max_ts FROM host_observations WHERE ok = 1"
        ).fetchone()
        most_recent_ok = row["max_ts"] if row else None

        total_row = conn.execute(
            "SELECT COUNT(DISTINCT host) AS n FROM host_observations"
        ).fetchone()
        ok_row = conn.execute(
            "SELECT COUNT(DISTINCT host) AS n FROM host_observations WHERE ok = 1"
        ).fetchone()
        hosts_total = total_row["n"] if total_row else 0
        hosts_with_ok = ok_row["n"] if ok_row else 0

    ok_age = age_seconds(most_recent_ok) if most_recent_ok else None

    if ok_age is None:
        status = "no_data"
    elif ok_age > threshold:
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
    threshold = result["stale_threshold_seconds"]
    print(f"status:          {status}")
    print(f"most_recent_ok:  {ok_ts} ({ok_age} ago)")
    print(f"hosts_with_ok:   {hosts_with_ok}/{hosts_total}")
    print(f"stale_threshold: {threshold}s")
