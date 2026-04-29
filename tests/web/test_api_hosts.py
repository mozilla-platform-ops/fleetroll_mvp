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


def _seed_host_with_os(temp_db: Path, host: str, os_type: str) -> None:
    conn = get_connection(temp_db)
    try:
        insert_host_observation(
            conn,
            {
                "host": host,
                "ts": "2026-04-27T00:00:00Z",
                "ok": 1,
                "observed": {"role": "test-role", "role_present": True, "os_type": os_type},
            },
        )
        conn.commit()
    finally:
        conn.close()


def test_hosts_filter_by_os(web_client: TestClient, temp_db: Path) -> None:
    _seed_host_with_os(temp_db, "linux1.example.com", "Linux")
    _seed_host_with_os(temp_db, "mac1.example.com", "Darwin")

    response = web_client.get("/api/hosts?filter=os%3DL")
    assert response.status_code == 200
    rows = response.json()["rows"]
    assert len(rows) == 1
    assert "linux1" in rows[0]["host"]
    assert rows[0]["os"] == "L"


def test_hosts_sort_by_host_desc(web_client: TestClient, temp_db: Path) -> None:
    _seed_host_with_os(temp_db, "aaa.example.com", "Linux")
    _seed_host_with_os(temp_db, "zzz.example.com", "Linux")

    response = web_client.get("/api/hosts?sort=host%3Adesc")
    assert response.status_code == 200
    rows = response.json()["rows"]
    assert len(rows) == 2
    assert rows[0]["host"] < rows[1]["host"] or rows[0]["host"] >= rows[1]["host"]
    hosts = [r["host"] for r in rows]
    assert hosts == sorted(hosts, reverse=True)


def test_hosts_combined_filter_and_sort(web_client: TestClient, temp_db: Path) -> None:
    _seed_host_with_os(temp_db, "alinux.example.com", "Linux")
    _seed_host_with_os(temp_db, "zlinux.example.com", "Linux")
    _seed_host_with_os(temp_db, "mac1.example.com", "Darwin")

    response = web_client.get("/api/hosts?filter=os%3DL&sort=host%3Adesc")
    assert response.status_code == 200
    rows = response.json()["rows"]
    assert len(rows) == 2
    assert all(r["os"] == "L" for r in rows)
    hosts = [r["host"] for r in rows]
    assert hosts == sorted(hosts, reverse=True)


def test_hosts_invalid_filter_returns_400(web_client: TestClient, temp_db: Path) -> None:
    _seed_host(temp_db, "host4.example.com")

    response = web_client.get("/api/hosts?filter=bogus_col%3Dx")
    assert response.status_code == 400
    body = response.json()
    assert "detail" in body
    assert body["detail"]


def test_hosts_empty_params_unchanged(web_client: TestClient, temp_db: Path) -> None:
    _seed_host(temp_db, "host5.example.com")

    response_plain = web_client.get("/api/hosts")
    response_empty = web_client.get("/api/hosts?filter=&sort=")
    assert response_plain.status_code == 200
    assert response_empty.status_code == 200
    assert response_plain.json()["rows"] == response_empty.json()["rows"]
