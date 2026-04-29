"""GET /api/hosts — return all host rows as rendered by host-monitor."""

from __future__ import annotations

from datetime import UTC, datetime
from importlib.metadata import version as get_version
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from fleetroll.commands.monitor.cache import ShaInfoCache
from fleetroll.commands.monitor.data import (
    age_seconds,
    build_row_values,
    detect_common_fqdn_suffix,
    most_recent_ok_ts,
    strip_fqdn,
)
from fleetroll.commands.monitor.query import apply_query, parse_query_safe, validate_query
from fleetroll.commands.web.schemas import HostRow, HostsResponse, HostsSummary
from fleetroll.constants import STALE_DATA_THRESHOLD_SECONDS
from fleetroll.data_provider import LocalProvider
from fleetroll.db import get_all_known_hosts, get_connection, get_db_path
from fleetroll.notes import default_notes_path, load_latest_notes
from fleetroll.utils import check_log_sizes

router = APIRouter()


@router.get("/api/hosts", response_model=HostsResponse)
def hosts(
    filter_expr: Annotated[
        str,
        Query(
            alias="filter",
            description="Filter expression using the host-monitor DSL (e.g. os=linux pp_last>1h)",
        ),
    ] = "",
    sort: Annotated[
        str,
        Query(
            description="Sort spec (e.g. pp_last:desc or host:asc). Also accepted inline in filter via sort: prefix.",
        ),
    ] = "",
) -> HostsResponse:
    raw = filter_expr.strip()
    if sort.strip():
        raw = f"{raw} sort:{sort.strip()}" if raw else f"sort:{sort.strip()}"
    q = parse_query_safe(raw)
    if raw:
        err = validate_query(q, raw)
        if err:
            raise HTTPException(status_code=400, detail=err)

    db_path = get_db_path()
    conn = get_connection(db_path)
    try:
        conn.commit()
        all_hosts = get_all_known_hosts(conn)
        provider = LocalProvider(conn)

        latest, latest_ok = provider.load_latest_records(hosts=all_hosts)
        tc_data = provider.load_tc_workers(hosts=all_hosts)
        github_refs = provider.load_github_refs()
        windows_pools = provider.load_windows_pools()

        fleetroll_dir = Path.home() / ".fleetroll"
        sha_cache = ShaInfoCache(fleetroll_dir / "overrides", fleetroll_dir / "vault_yamls")
        sha_cache.load_all()

        notes_data = load_latest_notes(default_notes_path())

        values_list = [
            build_row_values(
                host,
                latest.get(host),
                last_ok=latest_ok.get(host),
                tc_data=tc_data.get(strip_fqdn(host)),
                sha_cache=sha_cache,
                github_refs=github_refs,
                windows_pools=windows_pools,
                notes_data=notes_data,
            )
            for host in sorted(all_hosts)
        ]
        values_list = apply_query(values_list, q)
        rows = [HostRow(**v) for v in values_list]

        most_recent_ok = most_recent_ok_ts(latest_ok)
        ok_age = age_seconds(most_recent_ok) if most_recent_ok else None
        data_is_stale = ok_age is None or ok_age > STALE_DATA_THRESHOLD_SECONDS

        summary = HostsSummary(
            version=get_version("fleetroll"),
            db_path=str(db_path),
            total_hosts=len(all_hosts),
            fqdn_suffix=detect_common_fqdn_suffix(list(all_hosts)),
            log_size_warnings=check_log_sizes(),
            data_is_stale=data_is_stale,
        )
    finally:
        conn.close()

    return HostsResponse(rows=rows, generated_at=datetime.now(UTC), summary=summary)
