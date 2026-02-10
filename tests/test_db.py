"""Tests for SQLite database storage layer."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fleetroll.db import (
    get_connection,
    get_db_path,
    get_latest_github_refs,
    get_latest_host_observations,
    get_latest_tc_workers,
    init_db,
    insert_github_ref,
    insert_host_observation,
    insert_tc_worker,
)


@pytest.fixture
def temp_db():
    """Create temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    try:
        init_db(db_path)
        yield db_path
    finally:
        db_path.unlink(missing_ok=True)
        # Clean up WAL files
        Path(f"{db_path}-wal").unlink(missing_ok=True)
        Path(f"{db_path}-shm").unlink(missing_ok=True)


def test_get_db_path():
    """Test default database path."""
    db_path = get_db_path()
    assert db_path.name == "fleetroll.db"
    assert ".fleetroll" in str(db_path)


def test_init_db_creates_tables(temp_db):
    """Test database initialization creates all tables."""
    conn = get_connection(temp_db)
    try:
        # Check all tables exist
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]
        assert "host_observations" in tables
        assert "tc_workers" in tables
        assert "github_refs" in tables
    finally:
        conn.close()


def test_init_db_enables_wal_mode(temp_db):
    """Test WAL mode is enabled."""
    conn = get_connection(temp_db)
    try:
        cursor = conn.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        conn.close()


def test_insert_host_observation_basic(temp_db):
    """Test basic host observation insert."""
    conn = get_connection(temp_db)
    try:
        record = {
            "host": "host1.example.com",
            "ts": "2024-01-01T12:00:00+00:00",
            "ok": 1,
            "observed": {"role": "test_role"},
        }
        insert_host_observation(conn, record)
        conn.commit()

        # Verify insertion
        cursor = conn.execute(
            "SELECT host, ts, ok FROM host_observations WHERE host = ?",
            ("host1.example.com",),
        )
        row = cursor.fetchone()
        assert row["host"] == "host1.example.com"
        assert row["ts"] == "2024-01-01T12:00:00+00:00"
        assert row["ok"] == 1
    finally:
        conn.close()


def test_insert_host_observation_retention_limit(temp_db):
    """Test basic retention limit for host observations."""
    conn = get_connection(temp_db)
    try:
        host = "host1.example.com"

        # Insert 15 ok=1 records
        for i in range(15):
            record = {
                "host": host,
                "ts": f"2024-01-01T12:{i:02d}:00+00:00",
                "ok": 1,
                "observed": {},
            }
            insert_host_observation(conn, record, retention_limit=10)
            conn.commit()

        # Should have exactly 10 records
        cursor = conn.execute("SELECT COUNT(*) FROM host_observations WHERE host = ?", (host,))
        count = cursor.fetchone()[0]
        assert count == 10

        # Verify we kept the 10 most recent
        cursor = conn.execute(
            "SELECT ts FROM host_observations WHERE host = ? ORDER BY ts ASC",
            (host,),
        )
        timestamps = [row[0] for row in cursor.fetchall()]
        assert timestamps[0] == "2024-01-01T12:05:00+00:00"
        assert timestamps[-1] == "2024-01-01T12:14:00+00:00"
    finally:
        conn.close()


def test_insert_host_observation_preserves_last_ok(temp_db):
    """Test that last ok=1 record is preserved even when old."""
    conn = get_connection(temp_db)
    try:
        host = "host1.example.com"

        # Insert 5 ok=1 records
        for i in range(5):
            record = {
                "host": host,
                "ts": f"2024-01-01T12:{i:02d}:00+00:00",
                "ok": 1,
                "observed": {"test": f"ok_{i}"},
            }
            insert_host_observation(conn, record, retention_limit=10)
            conn.commit()

        # Insert 12 ok=0 records (should trigger cleanup)
        for i in range(5, 17):
            record = {
                "host": host,
                "ts": f"2024-01-01T12:{i:02d}:00+00:00",
                "ok": 0,
                "error": "test error",
            }
            insert_host_observation(conn, record, retention_limit=10)
            conn.commit()

        # Should have 11 records: 10 most recent + 1 last ok=1
        cursor = conn.execute("SELECT COUNT(*) FROM host_observations WHERE host = ?", (host,))
        count = cursor.fetchone()[0]
        assert count == 11

        # Verify last ok=1 record still exists
        cursor = conn.execute(
            "SELECT ts, ok FROM host_observations WHERE host = ? AND ok = 1",
            (host,),
        )
        ok_records = cursor.fetchall()
        assert len(ok_records) == 1
        assert ok_records[0]["ts"] == "2024-01-01T12:04:00+00:00"
    finally:
        conn.close()


def test_insert_host_observation_multiple_hosts(temp_db):
    """Test retention is per-host (hosts don't interfere)."""
    conn = get_connection(temp_db)
    try:
        # Insert 15 records for host1
        for i in range(15):
            record = {
                "host": "host1.example.com",
                "ts": f"2024-01-01T12:{i:02d}:00+00:00",
                "ok": 1,
                "observed": {},
            }
            insert_host_observation(conn, record, retention_limit=10)
            conn.commit()

        # Insert 15 records for host2
        for i in range(15):
            record = {
                "host": "host2.example.com",
                "ts": f"2024-01-01T12:{i:02d}:00+00:00",
                "ok": 1,
                "observed": {},
            }
            insert_host_observation(conn, record, retention_limit=10)
            conn.commit()

        # Each host should have exactly 10 records
        cursor = conn.execute("SELECT host, COUNT(*) as cnt FROM host_observations GROUP BY host")
        counts = {row["host"]: row["cnt"] for row in cursor.fetchall()}
        assert counts["host1.example.com"] == 10
        assert counts["host2.example.com"] == 10
    finally:
        conn.close()


def test_insert_tc_worker_basic(temp_db):
    """Test basic TC worker insert."""
    conn = get_connection(temp_db)
    try:
        record = {
            "host": "host1.example.com",
            "ts": "2024-01-01T12:00:00+00:00",
            "type": "worker",
            "quarantine_until": None,
        }
        insert_tc_worker(conn, record)
        conn.commit()

        # Verify insertion
        cursor = conn.execute(
            "SELECT host, ts, type FROM tc_workers WHERE host = ?",
            ("host1.example.com",),
        )
        row = cursor.fetchone()
        assert row["host"] == "host1.example.com"
        assert row["ts"] == "2024-01-01T12:00:00+00:00"
        assert row["type"] == "worker"
    finally:
        conn.close()


def test_insert_tc_worker_retention(temp_db):
    """Test TC worker retention limit."""
    conn = get_connection(temp_db)
    try:
        host = "host1.example.com"

        # Insert 15 records
        for i in range(15):
            record = {
                "host": host,
                "ts": f"2024-01-01T12:{i:02d}:00+00:00",
                "type": "worker",
            }
            insert_tc_worker(conn, record, retention_limit=10)
            conn.commit()

        # Should have exactly 10 records
        cursor = conn.execute("SELECT COUNT(*) FROM tc_workers WHERE host = ?", (host,))
        count = cursor.fetchone()[0]
        assert count == 10
    finally:
        conn.close()


def test_insert_github_ref_basic(temp_db):
    """Test basic GitHub ref insert."""
    conn = get_connection(temp_db)
    try:
        record = {
            "owner": "mozilla",
            "repo": "test_repo",
            "branch": "main",
            "ts": "2024-01-01T12:00:00+00:00",
            "sha": "test_sha_abc123",
            "type": "branch_ref",
        }
        insert_github_ref(conn, record)
        conn.commit()

        # Verify insertion
        cursor = conn.execute(
            "SELECT owner, repo, branch, sha FROM github_refs WHERE owner = ?",
            ("mozilla",),
        )
        row = cursor.fetchone()
        assert row["owner"] == "mozilla"
        assert row["repo"] == "test_repo"
        assert row["branch"] == "main"
        assert row["sha"] == "test_sha_abc123"
    finally:
        conn.close()


def test_insert_github_ref_retention(temp_db):
    """Test GitHub ref retention per branch."""
    conn = get_connection(temp_db)
    try:
        owner = "mozilla"
        repo = "test_repo"
        branch = "main"

        # Insert 15 records for same branch
        for i in range(15):
            record = {
                "owner": owner,
                "repo": repo,
                "branch": branch,
                "ts": f"2024-01-01T12:{i:02d}:00+00:00",
                "sha": f"test_sha_{i}",
                "type": "branch_ref",
            }
            insert_github_ref(conn, record, retention_limit=10)
            conn.commit()

        # Should have exactly 10 records
        cursor = conn.execute(
            "SELECT COUNT(*) FROM github_refs WHERE owner = ? AND repo = ? AND branch = ?",
            (owner, repo, branch),
        )
        count = cursor.fetchone()[0]
        assert count == 10
    finally:
        conn.close()


def test_insert_github_ref_multiple_branches(temp_db):
    """Test retention is per-branch (branches don't interfere)."""
    conn = get_connection(temp_db)
    try:
        owner = "mozilla"
        repo = "test_repo"

        # Insert 15 records for main branch
        for i in range(15):
            record = {
                "owner": owner,
                "repo": repo,
                "branch": "main",
                "ts": f"2024-01-01T12:{i:02d}:00+00:00",
                "sha": f"test_main_sha_{i}",
                "type": "branch_ref",
            }
            insert_github_ref(conn, record, retention_limit=10)
            conn.commit()

        # Insert 15 records for dev branch
        for i in range(15):
            record = {
                "owner": owner,
                "repo": repo,
                "branch": "dev",
                "ts": f"2024-01-01T12:{i:02d}:00+00:00",
                "sha": f"test_dev_sha_{i}",
                "type": "branch_ref",
            }
            insert_github_ref(conn, record, retention_limit=10)
            conn.commit()

        # Each branch should have exactly 10 records
        cursor = conn.execute(
            """
            SELECT branch, COUNT(*) as cnt
            FROM github_refs
            WHERE owner = ? AND repo = ?
            GROUP BY branch
            """,
            (owner, repo),
        )
        counts = {row["branch"]: row["cnt"] for row in cursor.fetchall()}
        assert counts["main"] == 10
        assert counts["dev"] == 10
    finally:
        conn.close()


def test_get_latest_host_observations_empty(temp_db):
    """Test getting latest observations from empty database."""
    conn = get_connection(temp_db)
    try:
        latest, latest_ok = get_latest_host_observations(conn, ["host1.example.com"])
        assert latest == {}
        assert latest_ok == {}
    finally:
        conn.close()


def test_get_latest_host_observations_basic(temp_db):
    """Test getting latest host observations."""
    conn = get_connection(temp_db)
    try:
        # Insert records for two hosts
        for host_num in [1, 2]:
            for i in range(3):
                record = {
                    "host": f"host{host_num}.example.com",
                    "ts": f"2024-01-01T12:{i:02d}:00+00:00",
                    "ok": 1,
                    "observed": {"iteration": i},
                }
                insert_host_observation(conn, record)
        conn.commit()

        # Get latest for both hosts
        latest, latest_ok = get_latest_host_observations(
            conn, ["host1.example.com", "host2.example.com"]
        )

        assert len(latest) == 2
        assert latest["host1.example.com"]["observed"]["iteration"] == 2
        assert latest["host2.example.com"]["observed"]["iteration"] == 2

        assert len(latest_ok) == 2
        assert latest_ok["host1.example.com"]["ok"] == 1
        assert latest_ok["host2.example.com"]["ok"] == 1
    finally:
        conn.close()


def test_get_latest_host_observations_with_failures(temp_db):
    """Test getting latest observations when current is failed."""
    conn = get_connection(temp_db)
    try:
        host = "host1.example.com"

        # Insert ok=1 record
        record_ok = {
            "host": host,
            "ts": "2024-01-01T12:00:00+00:00",
            "ok": 1,
            "observed": {"test": "ok_data"},
        }
        insert_host_observation(conn, record_ok)

        # Insert ok=0 record (more recent)
        record_fail = {
            "host": host,
            "ts": "2024-01-01T12:05:00+00:00",
            "ok": 0,
            "error": "test error",
        }
        insert_host_observation(conn, record_fail)
        conn.commit()

        latest, latest_ok = get_latest_host_observations(conn, [host])

        # Latest should be the failed record
        assert latest[host]["ok"] == 0
        assert latest[host]["ts"] == "2024-01-01T12:05:00+00:00"

        # latest_ok should be the successful record
        assert latest_ok[host]["ok"] == 1
        assert latest_ok[host]["ts"] == "2024-01-01T12:00:00+00:00"
    finally:
        conn.close()


def test_get_latest_tc_workers_empty(temp_db):
    """Test getting TC workers from empty database."""
    conn = get_connection(temp_db)
    try:
        result = get_latest_tc_workers(conn, ["host1.example.com"])
        assert result == {}
    finally:
        conn.close()


def test_get_latest_tc_workers_basic(temp_db):
    """Test getting latest TC worker data."""
    conn = get_connection(temp_db)
    try:
        # Insert records for two hosts
        for host_num in [1, 2]:
            for i in range(3):
                record = {
                    "host": f"host{host_num}.example.com",
                    "ts": f"2024-01-01T12:{i:02d}:00+00:00",
                    "type": "worker",
                    "iteration": i,
                }
                insert_tc_worker(conn, record)
        conn.commit()

        result = get_latest_tc_workers(conn, ["host1.example.com", "host2.example.com"])

        assert len(result) == 2
        assert result["host1.example.com"]["iteration"] == 2
        assert result["host2.example.com"]["iteration"] == 2
    finally:
        conn.close()


def test_get_latest_github_refs_empty(temp_db):
    """Test getting GitHub refs from empty database."""
    conn = get_connection(temp_db)
    try:
        result = get_latest_github_refs(conn)
        assert result == {}
    finally:
        conn.close()


def test_get_latest_github_refs_basic(temp_db):
    """Test getting latest GitHub refs."""
    conn = get_connection(temp_db)
    try:
        owner = "mozilla"
        repo = "test_repo"

        # Insert records for two branches
        for branch in ["main", "dev"]:
            for i in range(3):
                record = {
                    "owner": owner,
                    "repo": repo,
                    "branch": branch,
                    "ts": f"2024-01-01T12:{i:02d}:00+00:00",
                    "sha": f"test_sha_{branch}_{i}",
                    "type": "branch_ref",
                }
                insert_github_ref(conn, record)
        conn.commit()

        result = get_latest_github_refs(conn)

        assert len(result) == 2
        assert result["mozilla/test_repo:main"]["sha"] == "test_sha_main_2"
        assert result["mozilla/test_repo:dev"]["sha"] == "test_sha_dev_2"
    finally:
        conn.close()


def test_json_round_trip(temp_db):
    """Test JSON data is preserved through insert/retrieve cycle."""
    conn = get_connection(temp_db)
    try:
        host = "host1.example.com"
        complex_data = {
            "host": host,
            "ts": "2024-01-01T12:00:00+00:00",
            "ok": 1,
            "observed": {
                "role": "test_role",
                "override_sha256": "test_sha_xyz789",
                "nested": {"key": "value", "number": 42},
                "list": [1, 2, 3],
            },
        }

        insert_host_observation(conn, complex_data)
        conn.commit()

        latest, _ = get_latest_host_observations(conn, [host])
        retrieved = latest[host]

        # Verify all nested data preserved
        assert retrieved["observed"]["role"] == "test_role"
        assert retrieved["observed"]["override_sha256"] == "test_sha_xyz789"
        assert retrieved["observed"]["nested"]["key"] == "value"
        assert retrieved["observed"]["nested"]["number"] == 42
        assert retrieved["observed"]["list"] == [1, 2, 3]
    finally:
        conn.close()


def test_get_observations_since_empty(temp_db):
    """Returns empty list when no records exist."""
    from fleetroll.db import get_connection, get_observations_since

    conn = get_connection(temp_db)
    try:
        result = get_observations_since(conn, hosts=["host1"], after_ts="2024-01-01T00:00:00Z")
        assert result == []
    finally:
        conn.close()


def test_get_observations_since_returns_records_after_timestamp(temp_db):
    """Returns records newer than given timestamp."""
    from fleetroll.db import get_connection, get_observations_since, insert_host_observation

    conn = get_connection(temp_db)
    try:
        host = "host1.example.com"
        insert_host_observation(conn, {"host": host, "ts": "2024-01-01T10:00:00Z", "ok": 1})
        insert_host_observation(conn, {"host": host, "ts": "2024-01-01T11:00:00Z", "ok": 0})
        insert_host_observation(conn, {"host": host, "ts": "2024-01-01T12:00:00Z", "ok": 1})
        conn.commit()

        result = get_observations_since(conn, hosts=[host], after_ts="2024-01-01T10:30:00Z")

        assert len(result) == 2
        assert result[0]["ts"] == "2024-01-01T11:00:00Z"
        assert result[1]["ts"] == "2024-01-01T12:00:00Z"
    finally:
        conn.close()


def test_get_observations_since_filters_by_host_list(temp_db):
    """Only returns records for requested hosts."""
    from fleetroll.db import get_connection, get_observations_since, insert_host_observation

    conn = get_connection(temp_db)
    try:
        insert_host_observation(
            conn, {"host": "host1.example.com", "ts": "2024-01-01T10:00:00Z", "ok": 1}
        )
        insert_host_observation(
            conn, {"host": "host2.example.com", "ts": "2024-01-01T10:00:00Z", "ok": 1}
        )
        insert_host_observation(
            conn, {"host": "host3.example.com", "ts": "2024-01-01T10:00:00Z", "ok": 1}
        )
        conn.commit()

        result = get_observations_since(
            conn, hosts=["host1.example.com", "host3.example.com"], after_ts="2024-01-01T09:00:00Z"
        )

        assert len(result) == 2
        hosts_in_result = {r["host"] for r in result}
        assert hosts_in_result == {"host1.example.com", "host3.example.com"}
    finally:
        conn.close()


def test_get_observations_since_ordered_by_ts_ascending(temp_db):
    """Returns records in ascending timestamp order."""
    from fleetroll.db import get_connection, get_observations_since, insert_host_observation

    conn = get_connection(temp_db)
    try:
        host = "host1.example.com"
        insert_host_observation(conn, {"host": host, "ts": "2024-01-01T12:00:00Z", "ok": 1})
        insert_host_observation(conn, {"host": host, "ts": "2024-01-01T10:00:00Z", "ok": 1})
        insert_host_observation(conn, {"host": host, "ts": "2024-01-01T11:00:00Z", "ok": 0})
        conn.commit()

        result = get_observations_since(conn, hosts=[host], after_ts="2024-01-01T09:00:00Z")

        assert len(result) == 3
        assert result[0]["ts"] == "2024-01-01T10:00:00Z"
        assert result[1]["ts"] == "2024-01-01T11:00:00Z"
        assert result[2]["ts"] == "2024-01-01T12:00:00Z"
    finally:
        conn.close()


def test_get_observations_since_empty_hosts_list(temp_db):
    """Returns empty list when hosts list is empty."""
    from fleetroll.db import get_connection, get_observations_since

    conn = get_connection(temp_db)
    try:
        result = get_observations_since(conn, hosts=[], after_ts="2024-01-01T00:00:00Z")
        assert result == []
    finally:
        conn.close()
