"""Tests for monitor data loading functions."""

import tempfile
from pathlib import Path

import pytest
from fleetroll.commands.monitor import load_tc_worker_data_from_db
from fleetroll.db import get_connection, init_db, insert_tc_worker


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


def test_load_tc_worker_data_from_db_basic(temp_db) -> None:
    """Test loading TC worker data from SQLite."""
    conn = get_connection(temp_db)

    # Insert test data
    records = [
        {
            "type": "worker",
            "ts": "2026-02-10T10:00:00+00:00",
            "host": "host1.example.com",
            "worker_id": "host1",
            "provisioner": "releng-hardware",
            "worker_type": "gecko-t-linux",
            "state": "running",
            "last_date_active": "2026-02-10T09:55:00+00:00",
            "task_started": "2026-02-10T09:50:00+00:00",
            "quarantine_until": None,
        },
        {
            "type": "worker",
            "ts": "2026-02-10T10:00:00+00:00",
            "host": "host2.example.com",
            "worker_id": "host2",
            "provisioner": "releng-hardware",
            "worker_type": "gecko-t-linux",
            "state": "stopped",
            "last_date_active": "2026-02-10T08:00:00+00:00",
            "task_started": None,
            "quarantine_until": None,
        },
    ]

    for record in records:
        insert_tc_worker(conn, record)
    conn.commit()

    # Load data
    hosts = ["host1.example.com", "host2.example.com"]
    result = load_tc_worker_data_from_db(conn, hosts=hosts)

    # Verify results are keyed by short hostname
    assert "host1" in result
    assert "host2" in result
    assert result["host1"]["state"] == "running"
    assert result["host2"]["state"] == "stopped"


def test_load_tc_worker_data_from_db_empty(temp_db) -> None:
    """Test loading TC worker data with no records."""
    conn = get_connection(temp_db)

    hosts = ["host1.example.com"]
    result = load_tc_worker_data_from_db(conn, hosts=hosts)

    assert result == {}


def test_load_tc_worker_data_from_db_latest_only(temp_db) -> None:
    """Test that only latest records are returned."""
    conn = get_connection(temp_db)

    # Insert older record
    old_record = {
        "type": "worker",
        "ts": "2026-02-10T09:00:00+00:00",
        "host": "host1.example.com",
        "worker_id": "host1",
        "provisioner": "releng-hardware",
        "worker_type": "gecko-t-linux",
        "state": "stopped",
        "last_date_active": "2026-02-10T08:00:00+00:00",
        "task_started": None,
        "quarantine_until": None,
    }
    insert_tc_worker(conn, old_record)

    # Insert newer record
    new_record = {
        "type": "worker",
        "ts": "2026-02-10T10:00:00+00:00",
        "host": "host1.example.com",
        "worker_id": "host1",
        "provisioner": "releng-hardware",
        "worker_type": "gecko-t-linux",
        "state": "running",
        "last_date_active": "2026-02-10T09:55:00+00:00",
        "task_started": "2026-02-10T09:50:00+00:00",
        "quarantine_until": None,
    }
    insert_tc_worker(conn, new_record)
    conn.commit()

    # Load data
    hosts = ["host1.example.com"]
    result = load_tc_worker_data_from_db(conn, hosts=hosts)

    # Should return only the latest record
    assert result["host1"]["state"] == "running"
    assert result["host1"]["ts"] == "2026-02-10T10:00:00+00:00"
