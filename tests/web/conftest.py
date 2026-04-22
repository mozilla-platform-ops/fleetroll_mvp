"""Fixtures for web API tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fleetroll.commands.web.app import create_app
from fleetroll.commands.web.settings import WebSettings


@pytest.fixture
def web_client(temp_db: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr("fleetroll.commands.web.routes.health.get_db_path", lambda: temp_db)
    monkeypatch.setattr("fleetroll.commands.web.routes.hello.get_db_path", lambda: temp_db)
    settings = WebSettings(web_host="127.0.0.1", web_port=8765, web_dev=False)
    app = create_app(settings)
    return TestClient(app, raise_server_exceptions=False)
