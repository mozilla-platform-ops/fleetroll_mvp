"""Web server settings loaded from environment variables."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class WebSettings(BaseSettings):
    web_host: str = "127.0.0.1"
    web_port: int = 8765
    web_dev: bool = False

    model_config = SettingsConfigDict(env_prefix="FLEETROLL_")
