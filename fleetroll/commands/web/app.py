"""FastAPI application factory."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .logging import RequestIDMiddleware, configure_structlog
from .routes.filters import router as filters_router
from .routes.health import router as health_router
from .routes.hello import router as hello_router
from .routes.hosts import router as hosts_router
from .settings import WebSettings
from .static import mount_static


def create_app(settings: WebSettings | None = None) -> FastAPI:
    if settings is None:
        settings = WebSettings()

    configure_structlog()

    app = FastAPI(title="fleetroll", version=version("fleetroll"))

    app.add_middleware(RequestIDMiddleware)  # type: ignore[arg-type]

    if settings.web_dev:
        app.add_middleware(
            CORSMiddleware,  # type: ignore[arg-type]
            allow_origins=["http://localhost:5173"],
            allow_methods=["GET"],
            allow_headers=["*"],
        )

    app.include_router(filters_router)
    app.include_router(health_router)
    app.include_router(hello_router)
    app.include_router(hosts_router)

    # FUTURE: mount Prometheus instrumentator or OTel middleware here (mvp-kaw5.5)

    dist_dir = Path(__file__).parent.parent.parent.parent / "web" / "dist"
    mount_static(app, dist_dir)

    return app
