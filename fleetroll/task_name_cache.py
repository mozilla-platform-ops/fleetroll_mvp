"""Disposable task-name metadata, separate from recorded configuration evidence."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path

RETENTION_SECONDS = 30 * 24 * 60 * 60
MAX_ENTRIES = 50_000


def load_names(path: Path, *, root_url: str) -> dict[str, str]:
    """Read recent names; do not create a database on a cache miss."""
    if not path.exists():
        return {}
    with closing(sqlite3.connect(path, timeout=0.2)) as conn:
        return dict(
            conn.execute(
                "SELECT task_id, name FROM task_names WHERE root_url = ? AND fetched_at > ?",
                (root_url, time.time() - RETENTION_SECONDS),
            )
        )


def save_names(path: Path, *, root_url: str, names: dict[str, str]) -> None:
    """Save successful lookups and bound retention across all service roots."""
    with closing(sqlite3.connect(path, timeout=0.2)) as conn, conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_names (
                root_url TEXT NOT NULL,
                task_id TEXT NOT NULL,
                name TEXT NOT NULL,
                fetched_at REAL NOT NULL,
                PRIMARY KEY (root_url, task_id)
            )
        """)
        now = time.time()
        conn.executemany(
            "INSERT OR REPLACE INTO task_names VALUES (?, ?, ?, ?)",
            [(root_url, task_id, name, now) for task_id, name in names.items()],
        )
        conn.execute("DELETE FROM task_names WHERE fetched_at <= ?", (now - RETENTION_SECONDS,))
        conn.execute(
            "DELETE FROM task_names WHERE rowid IN "
            "(SELECT rowid FROM task_names ORDER BY fetched_at DESC, rowid DESC LIMIT -1 OFFSET ?)",
            (MAX_ENTRIES,),
        )
