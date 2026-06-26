"""Tests for built web static assets."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_favicon_served(web_client: TestClient) -> None:
    response = web_client.get("/favicon.svg")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert 'aria-label="fleetroll"' in response.text
