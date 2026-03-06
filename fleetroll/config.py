"""FleetRoll configuration loading."""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path

from .constants import AUDIT_DIR_NAME, CONFIG_FILE_NAME

logger = logging.getLogger(__name__)


def load_config() -> dict:
    """Load ~/.fleetroll/config.toml, returning {} if missing or invalid."""
    config_path = Path.home() / AUDIT_DIR_NAME / CONFIG_FILE_NAME
    if not config_path.exists():
        logger.debug("Config file not found: %s", config_path)
        return {}
    try:
        with config_path.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError:
        logger.debug("Failed to parse config file: %s", config_path)
        return {}
