"""Disposable task-name metadata, separate from recorded configuration evidence."""

from __future__ import annotations

import concurrent.futures
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import TypedDict

from .taskcluster import TC_ROOT_URL

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


class TaskNameLookups(TypedDict):
    cached: int
    reused: int
    fetched: int
    failed: int
    warnings: list[str]


def enrich_task_names(
    hosts, *, clients, workers, progress=None, cache_path=None, root_url=TC_ROOT_URL
):
    """Fetch names for successes too, without discarding outcomes on metadata errors."""
    jobs_by_task = {}
    for host in hosts:
        for job in host["jobs"]:
            if job["in_window"] and not job.get("query_error") and job.get("task_id"):
                jobs_by_task.setdefault(job["task_id"], []).append(job)

    stats: TaskNameLookups = {"cached": 0, "reused": 0, "fetched": 0, "failed": 0, "warnings": []}
    cached = {}
    if cache_path is not None:
        try:
            cached = load_names(cache_path, root_url=root_url)
        except (OSError, sqlite3.Error) as exc:
            stats["warnings"].append(
                f"Task-name cache read unavailable; using live metadata: {exc}"
            )
    resolved = {}
    fresh = {}
    for task_id, jobs in jobs_by_task.items():
        known = next(
            (
                job["name"]
                for job in jobs
                if isinstance(job.get("name"), str) and job["name"].strip()
            ),
            None,
        )
        if known:
            resolved[task_id] = known, None
            fresh[task_id] = known
            stats["reused"] += 1
        elif task_id in cached:
            resolved[task_id] = cached[task_id], None
            stats["cached"] += 1

    def fetch(task_id):
        try:
            name = (clients.queue.task(task_id).get("metadata") or {}).get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("task metadata name missing")
            return name, None
        except Exception as exc:
            return None, str(exc)

    missing = jobs_by_task.keys() - resolved.keys()

    def update():
        if progress:
            progress(
                "Fetching task names",
                completed=stats["fetched"] + stats["failed"],
                total=len(missing),
                detail=f"{stats['cached']} cached, {stats['reused']} reused, {stats['failed']} errors",
            )

    update()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch, task_id): task_id for task_id in missing}
        for future in concurrent.futures.as_completed(futures):
            name, error = future.result()
            task_id = futures[future]
            resolved[task_id] = name, error
            if error:
                stats["failed"] += 1
            else:
                stats["fetched"] += 1
            if name is not None:
                fresh[task_id] = name
            update()
    for task_id, (name, error) in resolved.items():
        for job in jobs_by_task[task_id]:
            job["name"], job["name_error"] = name, error
    if cache_path is not None:
        try:
            save_names(cache_path, root_url=root_url, names=fresh)
        except (OSError, sqlite3.Error) as exc:
            stats["warnings"].append(
                f"Task-name cache write unavailable; live results retained: {exc}"
            )
    return stats
