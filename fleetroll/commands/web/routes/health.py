"""GET /api/health — real DB readiness probe."""

from __future__ import annotations

from importlib.metadata import version

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from fleetroll.commands.web.schemas import HealthResponse
from fleetroll.db import get_connection, get_db_path

router = APIRouter()


@router.get("/api/health", response_model=HealthResponse)
def health() -> JSONResponse:
    fleetroll_version = version("fleetroll")
    try:
        db_path = get_db_path()
        conn = get_connection(db_path)
        conn.execute("SELECT 1")
        conn.close()
        db_ok = True
    except Exception:
        db_ok = False

    payload = HealthResponse(ok=db_ok, db_ok=db_ok, version=fleetroll_version)
    status_code = 200 if db_ok else 503
    return JSONResponse(content=payload.model_dump(), status_code=status_code)
