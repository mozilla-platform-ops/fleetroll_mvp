"""SQLite database storage layer for FleetRoll.

Provides storage for high-frequency data:
- host_observations: Host audit records
- tc_workers: TaskCluster worker data
- github_refs: GitHub branch references

All tables use hybrid schema (indexed columns + JSON blob) and automatic
retention limiting to prevent unbounded growth.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .constants import AUDIT_DIR_NAME, DB_FILE_NAME, DB_RETENTION_LIMIT


def get_db_path() -> Path:
    """Return path to SQLite database file.

    Returns:
        Path to ~/.fleetroll/fleetroll.db
    """
    home = Path(os.path.expanduser("~"))
    return home / AUDIT_DIR_NAME / DB_FILE_NAME


def init_db(db_path: Path) -> None:
    """Initialize database schema and enable WAL mode.

    Creates all required tables if they don't exist and enables
    Write-Ahead Logging for better concurrent access.

    Args:
        db_path: Path to database file
    """
    # Ensure parent directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        # Enable WAL mode for better concurrent access
        conn.execute("PRAGMA journal_mode=WAL")

        # Host observations table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS host_observations (
                host TEXT NOT NULL,
                ts TEXT NOT NULL,
                ok INTEGER NOT NULL,
                data JSON NOT NULL,
                PRIMARY KEY (host, ts)
            )
        """)

        # TaskCluster workers table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tc_workers (
                host TEXT NOT NULL,
                ts TEXT NOT NULL,
                type TEXT NOT NULL,
                data JSON NOT NULL,
                PRIMARY KEY (host, ts)
            )
        """)

        # GitHub refs table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS github_refs (
                owner TEXT NOT NULL,
                repo TEXT NOT NULL,
                branch TEXT NOT NULL,
                ts TEXT NOT NULL,
                sha TEXT NOT NULL,
                data JSON NOT NULL,
                PRIMARY KEY (owner, repo, branch, ts)
            )
        """)

        conn.commit()
    finally:
        conn.close()


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Get database connection with JSON support enabled.

    Args:
        db_path: Path to database file

    Returns:
        SQLite connection configured for dict-like row access
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def insert_host_observation(
    conn: sqlite3.Connection,
    record: dict[str, Any],
    *,
    retention_limit: int = DB_RETENTION_LIMIT,
) -> None:
    """Insert host observation record with dual retention policy.

    Keeps latest N records per host, plus always preserves the most recent
    ok=1 record even if it falls outside the N-record window. This ensures
    "last known good state" is never lost during extended failures.

    Args:
        conn: Database connection
        record: Host observation record (must have host, ts, ok fields)
        retention_limit: Number of recent records to keep per host

    Raises:
        KeyError: If record is missing required fields
    """
    host = record["host"]
    ts = record["ts"]
    ok = record.get("ok", 0)
    data_json = json.dumps(record)

    # Insert new record
    conn.execute(
        """
        INSERT INTO host_observations (host, ts, ok, data)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (host, ts) DO UPDATE SET ok=?, data=?
        """,
        (host, ts, ok, data_json, ok, data_json),
    )

    # Find most recent ok=1 record
    last_ok_row = conn.execute(
        """
        SELECT rowid FROM host_observations
        WHERE host = ? AND ok = 1
        ORDER BY ts DESC, rowid DESC
        LIMIT 1
        """,
        (host,),
    ).fetchone()

    # Delete old records, preserving last ok=1
    if last_ok_row:
        last_ok_rowid = last_ok_row[0]
        conn.execute(
            """
            DELETE FROM host_observations
            WHERE host = ?
              AND rowid NOT IN (
                  SELECT rowid FROM host_observations
                  WHERE host = ?
                  ORDER BY ts DESC, rowid DESC
                  LIMIT ?
              )
              AND rowid != ?
            """,
            (host, host, retention_limit, last_ok_rowid),
        )
    else:
        # No ok=1 record exists, use simple retention
        conn.execute(
            """
            DELETE FROM host_observations
            WHERE host = ?
              AND rowid NOT IN (
                  SELECT rowid FROM host_observations
                  WHERE host = ?
                  ORDER BY ts DESC, rowid DESC
                  LIMIT ?
              )
            """,
            (host, host, retention_limit),
        )


def insert_tc_worker(
    conn: sqlite3.Connection,
    record: dict[str, Any],
    *,
    retention_limit: int = DB_RETENTION_LIMIT,
) -> None:
    """Insert TaskCluster worker record with simple retention.

    Keeps latest N records per host.

    Args:
        conn: Database connection
        record: TC worker record (must have host, ts, type fields)
        retention_limit: Number of recent records to keep per host

    Raises:
        KeyError: If record is missing required fields
    """
    host = record["host"]
    ts = record["ts"]
    worker_type = record.get("type", "worker")
    data_json = json.dumps(record)

    # Insert new record
    conn.execute(
        """
        INSERT INTO tc_workers (host, ts, type, data)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (host, ts) DO UPDATE SET type=?, data=?
        """,
        (host, ts, worker_type, data_json, worker_type, data_json),
    )

    # Delete old records beyond retention limit
    conn.execute(
        """
        DELETE FROM tc_workers
        WHERE host = ?
          AND rowid NOT IN (
              SELECT rowid FROM tc_workers
              WHERE host = ?
              ORDER BY ts DESC, rowid DESC
              LIMIT ?
          )
        """,
        (host, host, retention_limit),
    )


def insert_github_ref(
    conn: sqlite3.Connection,
    record: dict[str, Any],
    *,
    retention_limit: int = DB_RETENTION_LIMIT,
) -> None:
    """Insert GitHub ref record with simple retention.

    Keeps latest N records per (owner, repo, branch) combination.

    Args:
        conn: Database connection
        record: GitHub ref record (must have owner, repo, branch, ts, sha fields)
        retention_limit: Number of recent records to keep per branch

    Raises:
        KeyError: If record is missing required fields
    """
    owner = record["owner"]
    repo = record["repo"]
    branch = record["branch"]
    ts = record["ts"]
    sha = record["sha"]
    data_json = json.dumps(record)

    # Insert new record
    conn.execute(
        """
        INSERT INTO github_refs (owner, repo, branch, ts, sha, data)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (owner, repo, branch, ts) DO UPDATE SET sha=?, data=?
        """,
        (owner, repo, branch, ts, sha, data_json, sha, data_json),
    )

    # Delete old records beyond retention limit
    conn.execute(
        """
        DELETE FROM github_refs
        WHERE owner = ? AND repo = ? AND branch = ?
          AND rowid NOT IN (
              SELECT rowid FROM github_refs
              WHERE owner = ? AND repo = ? AND branch = ?
              ORDER BY ts DESC, rowid DESC
              LIMIT ?
          )
        """,
        (owner, repo, branch, owner, repo, branch, retention_limit),
    )


def get_latest_host_observations(
    conn: sqlite3.Connection,
    hosts: list[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Get latest host observation records.

    Returns two dicts:
    - latest: Most recent record per host (any status)
    - latest_ok: Most recent ok=1 record per host

    Args:
        conn: Database connection
        hosts: List of hostnames to query

    Returns:
        Tuple of (latest, latest_ok) dicts mapping hostname to record
    """
    if not hosts:
        return {}, {}

    latest: dict[str, dict[str, Any]] = {}
    latest_ok: dict[str, dict[str, Any]] = {}

    # Get latest record per host
    placeholders = ",".join("?" * len(hosts))
    rows = conn.execute(
        f"""
        SELECT host, data
        FROM host_observations
        WHERE (host, ts) IN (
            SELECT host, MAX(ts)
            FROM host_observations
            WHERE host IN ({placeholders})
            GROUP BY host
        )
        """,
        hosts,
    ).fetchall()

    for row in rows:
        host = row["host"]
        data = json.loads(row["data"])
        latest[host] = data

        if data.get("ok"):
            latest_ok[host] = data

    # Get latest ok=1 per host (for hosts that don't have ok=1 as latest)
    rows_ok = conn.execute(
        f"""
        SELECT host, data
        FROM host_observations
        WHERE ok = 1 AND (host, ts) IN (
            SELECT host, MAX(ts)
            FROM host_observations
            WHERE ok = 1 AND host IN ({placeholders})
            GROUP BY host
        )
        """,
        hosts,
    ).fetchall()

    for row in rows_ok:
        host = row["host"]
        data = json.loads(row["data"])
        if host not in latest_ok:
            latest_ok[host] = data

    return latest, latest_ok


def get_observations_since(
    conn: sqlite3.Connection,
    *,
    hosts: list[str],
    after_ts: str,
) -> list[dict[str, Any]]:
    """Get host observation records newer than a given timestamp.

    Returns records ordered by ts ascending, suitable for tailing.

    Args:
        conn: Database connection
        hosts: List of hostnames to query
        after_ts: ISO timestamp; only records with ts > this are returned

    Returns:
        List of record dicts ordered by ts ascending
    """
    if not hosts:
        return []

    placeholders = ",".join("?" * len(hosts))
    rows = conn.execute(
        f"""
        SELECT data FROM host_observations
        WHERE host IN ({placeholders}) AND ts > ?
        ORDER BY ts ASC
        """,
        [*hosts, after_ts],
    ).fetchall()

    return [json.loads(row["data"]) for row in rows]


def get_latest_tc_workers(
    conn: sqlite3.Connection,
    hosts: list[str],
) -> dict[str, dict[str, Any]]:
    """Get latest TaskCluster worker data per host.

    Args:
        conn: Database connection
        hosts: List of hostnames to query

    Returns:
        Dict mapping hostname to most recent worker record
    """
    if not hosts:
        return {}

    result: dict[str, dict[str, Any]] = {}

    placeholders = ",".join("?" * len(hosts))
    rows = conn.execute(
        f"""
        SELECT host, data
        FROM tc_workers
        WHERE (host, ts) IN (
            SELECT host, MAX(ts)
            FROM tc_workers
            WHERE host IN ({placeholders})
            GROUP BY host
        )
        """,
        hosts,
    ).fetchall()

    for row in rows:
        host = row["host"]
        data = json.loads(row["data"])
        result[host] = data

    return result


def get_latest_github_refs(
    conn: sqlite3.Connection,
) -> dict[str, dict[str, Any]]:
    """Get latest GitHub ref data for all branches.

    Returns:
        Dict mapping 'owner/repo:branch' to most recent ref record
    """
    result: dict[str, dict[str, Any]] = {}

    rows = conn.execute("""
        SELECT owner, repo, branch, data
        FROM github_refs
        WHERE (owner, repo, branch, ts) IN (
            SELECT owner, repo, branch, MAX(ts)
            FROM github_refs
            GROUP BY owner, repo, branch
        )
    """).fetchall()

    for row in rows:
        owner = row["owner"]
        repo = row["repo"]
        branch = row["branch"]
        data = json.loads(row["data"])
        key = f"{owner}/{repo}:{branch}"
        result[key] = data

    return result
