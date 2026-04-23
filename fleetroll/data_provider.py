"""DataProvider protocol and LocalProvider implementation for monitor data access."""

from __future__ import annotations

import sqlite3
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Tailer(Protocol):
    """Non-blocking source of incoming host observation records."""

    def poll(self) -> dict[str, Any] | None: ...


@runtime_checkable
class DataProvider(Protocol):
    """Abstract interface over a host-observation data source."""

    def load_latest_records(self, *, hosts: list[str]) -> tuple[dict[str, Any], dict[str, Any]]: ...

    def load_tc_workers(self, *, hosts: list[str]) -> dict[str, dict[str, Any]]: ...

    def load_github_refs(self) -> dict[str, dict[str, Any]]: ...

    def load_windows_pools(self) -> dict[str, dict[str, Any]]: ...

    def make_tailer(self, *, hosts: list[str]) -> Tailer: ...


class LocalProvider:
    """DataProvider backed by the local SQLite database via db.py."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def load_latest_records(self, *, hosts: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
        from .commands.monitor.data import load_latest_records

        return load_latest_records(self._conn, hosts=hosts)

    def load_tc_workers(self, *, hosts: list[str]) -> dict[str, dict[str, Any]]:
        from .commands.monitor.data import load_tc_worker_data_from_db

        self._conn.commit()
        return load_tc_worker_data_from_db(self._conn, hosts=hosts)

    def load_github_refs(self) -> dict[str, dict[str, Any]]:
        from .commands.monitor.data import load_github_refs_from_db

        self._conn.commit()
        return load_github_refs_from_db(self._conn)

    def load_windows_pools(self) -> dict[str, dict[str, Any]]:
        from .commands.monitor.data import load_windows_pools_from_db

        self._conn.commit()
        return load_windows_pools_from_db(self._conn)

    def make_tailer(self, *, hosts: list[str]) -> Tailer:
        from .commands.monitor.data import AuditLogTailer

        return AuditLogTailer(self._conn, hosts=hosts)
