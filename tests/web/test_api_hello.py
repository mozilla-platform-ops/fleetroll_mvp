"""Tests for GET /api/hello."""

from __future__ import annotations

from importlib.metadata import version

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.web


def test_hello_response_shape(web_client: TestClient) -> None:
    response = web_client.get("/api/hello")
    assert response.status_code == 200
    body = response.json()
    assert body["message"] == "Hello, fleetroll"
    assert body["db_ok"] is True
    assert body["version"] == version("fleetroll")


def test_hello_version_is_string(web_client: TestClient) -> None:
    response = web_client.get("/api/hello")
    body = response.json()
    assert isinstance(body["version"], str)
    assert len(body["version"]) > 0
