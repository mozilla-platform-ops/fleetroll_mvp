"""Tests for GET /api/filters endpoint."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fleetroll.commands.web.app import create_app
from fleetroll.commands.web.routes import filters as filters_module


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    filters_dir = tmp_path / "filters"
    filters_dir.mkdir()
    monkeypatch.setattr(filters_module, "_FILTERS_DIR", filters_dir)
    return TestClient(create_app())


def test_empty_when_no_yamls(client: TestClient) -> None:
    resp = client.get("/api/filters")
    assert resp.status_code == 200
    assert resp.json() == []


def test_returns_named_filters(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    filters_dir = tmp_path / "filters2"
    filters_dir.mkdir()
    (filters_dir / "healthy.yaml").write_text("query: healthy=Y\ndescription: Only healthy hosts\n")
    (filters_dir / "quarantined.yaml").write_text("query: tc_quar!=\n")
    monkeypatch.setattr(filters_module, "_FILTERS_DIR", filters_dir)
    client2 = TestClient(create_app())

    resp = client2.get("/api/filters")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    names = {f["name"] for f in data}
    assert names == {"healthy", "quarantined"}
    healthy = next(f for f in data if f["name"] == "healthy")
    assert healthy["query"] == "healthy=Y"
    assert healthy["description"] == "Only healthy hosts"


def test_malformed_yaml_skipped(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    filters_dir = tmp_path / "filters3"
    filters_dir.mkdir()
    (filters_dir / "good.yaml").write_text("query: os=L\n")
    (filters_dir / "bad.yaml").write_text(": broken: yaml: [unterminated\n")
    monkeypatch.setattr(filters_module, "_FILTERS_DIR", filters_dir)
    client3 = TestClient(create_app())

    resp = client3.get("/api/filters")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "good"
