"""GET /api/hello — hello-world endpoint with DB ping."""

from __future__ import annotations

from importlib.metadata import version

from fastapi import APIRouter

from fleetroll.commands.web.schemas import HelloResponse
from fleetroll.db import get_connection, get_db_path

router = APIRouter()


@router.get("/api/hello", response_model=HelloResponse)
def hello() -> HelloResponse:
    fleetroll_version = version("fleetroll")
    try:
        db_path = get_db_path()
        conn = get_connection(db_path)
        conn.execute("SELECT 1")
        conn.close()
        db_ok = True
    except Exception:
        db_ok = False

    return HelloResponse(message="Hello, fleetroll", version=fleetroll_version, db_ok=db_ok)
