"""GET /api/hosts — return all host rows as rendered by host-monitor."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter

from fleetroll.commands.monitor.cache import ShaInfoCache
from fleetroll.commands.monitor.data import build_row_values, strip_fqdn
from fleetroll.commands.web.schemas import HostRow, HostsResponse
from fleetroll.data_provider import LocalProvider
from fleetroll.db import get_all_known_hosts, get_connection, get_db_path
from fleetroll.notes import default_notes_path, load_latest_notes

router = APIRouter()


@router.get("/api/hosts", response_model=HostsResponse)
def hosts() -> HostsResponse:
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

        rows = []
        for host in sorted(all_hosts):
            values = build_row_values(
                host,
                latest.get(host),
                last_ok=latest_ok.get(host),
                tc_data=tc_data.get(strip_fqdn(host)),
                sha_cache=sha_cache,
                github_refs=github_refs,
                windows_pools=windows_pools,
                notes_data=notes_data,
            )
            rows.append(HostRow(**values))
    finally:
        conn.close()

    return HostsResponse(rows=rows, generated_at=datetime.now(UTC))
