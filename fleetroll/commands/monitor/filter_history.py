from __future__ import annotations

import contextlib
import logging
import sqlite3
import time
from pathlib import Path

from fleetroll.constants import AUDIT_DIR_NAME, FILTER_HISTORY_FILE_NAME, FILTER_HISTORY_MAX

log = logging.getLogger(__name__)


def filter_history_path() -> Path:
    return Path.home() / AUDIT_DIR_NAME / FILTER_HISTORY_FILE_NAME


def load_filter_history(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries = [line for line in lines if line.strip()]
    return entries[-FILTER_HISTORY_MAX:]


def save_filter_history(path: Path, history: list[str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(history[-FILTER_HISTORY_MAX:]) + "\n" if history else "",
            encoding="utf-8",
        )
    except OSError:
        pass


def dedupe_append(history: list[str], text: str) -> None:
    """Remove any prior equal entry then append text (LRU-style, in-place)."""
    with contextlib.suppress(ValueError):
        history.remove(text)
    history.append(text)
    if len(history) > FILTER_HISTORY_MAX:
        del history[: len(history) - FILTER_HISTORY_MAX]


def record_filter_use(path: Path, *, query: str) -> None:
    """Increment usage independently of the recents file and other sessions."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(sqlite3.connect(path, timeout=0.2)) as conn, conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS filter_usage (
                    query TEXT PRIMARY KEY,
                    uses INTEGER NOT NULL,
                    last_used INTEGER NOT NULL
                )
            """)
            conn.execute(
                """
                INSERT INTO filter_usage VALUES (?, 1, ?)
                ON CONFLICT(query) DO UPDATE SET
                    uses = filter_usage.uses + 1,
                    last_used = MAX(filter_usage.last_used, excluded.last_used)
                """,
                (query, time.time_ns()),
            )
    except (OSError, sqlite3.Error) as exc:
        log.warning("could not record filter usage: %s", exc)


def load_most_used_filters(path: Path) -> list[tuple[str, int]]:
    """Return query counts ranked by frequency, with newest use first on ties."""
    try:
        with contextlib.closing(
            sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=0.2)
        ) as conn:
            return conn.execute(
                "SELECT query, uses FROM filter_usage ORDER BY uses DESC, last_used DESC, query"
            ).fetchall()
    except (OSError, sqlite3.Error) as exc:
        if path.exists():
            log.warning("could not load filter usage: %s", exc)
        return []
