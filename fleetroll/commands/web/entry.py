"""Entry point for the fleetroll web command."""

from __future__ import annotations

import uvicorn

from .app import create_app
from .settings import WebSettings


def cmd_web(*, host: str, port: int, dev: bool) -> None:
    settings = WebSettings(web_host=host, web_port=port, web_dev=dev)
    app = create_app(settings)
    uvicorn.run(app, host=host, port=port)
