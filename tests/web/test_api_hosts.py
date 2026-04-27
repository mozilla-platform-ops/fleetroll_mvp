"""Tests for GET /api/hosts."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fleetroll.db import get_connection, insert_host_observation

pytestmark = pytest.mark.web


def _seed_host(temp_db: Path, host: str, ok: int = 1) -> None:
    conn = get_connection(temp_db)
    try:
        insert_host_observation(
            conn,
            {
                "host": host,
                "ts": "2026-04-27T00:00:00Z",
                "ok": ok,
                "observed": {"role": "t-linux-talos", "role_present": True, "os_type": "Linux"},
            },
        )
        conn.commit()
    finally:
        conn.close()


def test_hosts_empty_db(web_client: TestClient) -> None:
    response = web_client.get("/api/hosts")
    assert response.status_code == 200
    body = response.json()
    assert body["rows"] == []
    assert "generated_at" in body


def test_hosts_returns_seeded_row(web_client: TestClient, temp_db: Path) -> None:
    _seed_host(temp_db, "host1.example.com")

    response = web_client.get("/api/hosts")
    assert response.status_code == 200
    body = response.json()
    rows = body["rows"]
    assert len(rows) == 1
    row = rows[0]
    assert "host1" in row["host"]
    assert row["status"] == "OK"
    assert row["os"] == "L"
    assert row["role"] == "t-linux-talos"
    assert row["healthy"] in ("Y", "N", "-")


def test_hosts_row_has_all_fields(web_client: TestClient, temp_db: Path) -> None:
    _seed_host(temp_db, "host2.example.com")

    response = web_client.get("/api/hosts")
    body = response.json()
    row = body["rows"][0]

    expected_fields = {
        "status",
        "host",
        "uptime",
        "override",
        "role",
        "os",
        "sha",
        "vlt_sha",
        "mtime",
        "err",
        "tc_quar",
        "tc_act",
        "tc_j_sf",
        "pp_last",
        "pp_sha",
        "pp_exp",
        "pp_match",
        "healthy",
        "data",
        "note",
    }
    assert expected_fields.issubset(row.keys())


def test_hosts_unk_row_for_missing_record(web_client: TestClient, temp_db: Path) -> None:
    _seed_host(temp_db, "host3.example.com", ok=0)

    response = web_client.get("/api/hosts")
    body = response.json()
    rows = body["rows"]
    assert len(rows) == 1
    assert rows[0]["status"] in ("OK", "FAIL", "UNK")
