"""Static file serving for built frontend assets."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles


def mount_static(app: FastAPI, dist_dir: Path) -> None:
    if dist_dir.exists():
        app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="static")
    else:
        # Dev hint — no dist/ present; fall through to JSON response
        @app.get("/", include_in_schema=False)
        async def _dev_hint() -> JSONResponse:
            return JSONResponse(
                {
                    "hint": "Frontend not built. Run: pnpm --dir web build",
                    "dev": "Or start Vite: pnpm --dir web dev (then open http://localhost:5173)",
                },
                status_code=404,
            )
