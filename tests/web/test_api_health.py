"""Tests for GET /api/health."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.web


def test_health_ok(web_client: TestClient) -> None:
    response = web_client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["db_ok"] is True
    assert isinstance(body["version"], str)
    assert len(body["version"]) > 0


def test_health_db_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, web_client: TestClient
) -> None:
    # Point to a path whose parent directory doesn't exist — sqlite3 will fail to open it
    bad_path = tmp_path / "nonexistent_subdir" / "db.db"
    monkeypatch.setattr("fleetroll.commands.web.routes.health.get_db_path", lambda: bad_path)
    response = web_client.get("/api/health")
    assert response.status_code == 503
    body = response.json()
    assert body["ok"] is False
    assert body["db_ok"] is False
