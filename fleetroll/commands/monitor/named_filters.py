"""Load named filters from configs/filters/*.yaml for the host-monitor picker."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class NamedFilter:
    """A named filter loaded from configs/filters/<name>.yaml.

    `name` is the filename stem; the YAML file itself has no `name:` field.
    """

    name: str
    query: str
    description: str = ""


def load_named_filters(configs_dir: Path) -> list[NamedFilter]:
    """Load every *.yaml file in configs_dir as a NamedFilter.

    The directory is created if it does not exist. Malformed files are skipped
    with a log warning; the monitor should never crash on bad filter YAML.

    Returns:
        Filters sorted alphabetically by name.
    """
    try:
        configs_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("could not create filters dir %s: %s", configs_dir, exc)
        return []

    filters: list[NamedFilter] = []
    for path in sorted(configs_dir.glob("*.yaml")):
        parsed = _parse_filter_file(path)
        if parsed is not None:
            filters.append(parsed)
    filters.sort(key=lambda f: f.name)
    return filters


def _parse_filter_file(path: Path) -> NamedFilter | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("could not read filter file %s: %s", path, exc)
        return None
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        log.warning("skipping malformed filter YAML %s: %s", path, exc)
        return None
    if not isinstance(data, dict):
        log.warning("skipping filter file %s: top-level must be a mapping", path)
        return None
    query = data.get("query")
    if not isinstance(query, str) or not query.strip():
        log.warning("skipping filter file %s: missing or empty 'query'", path)
        return None
    description = data.get("description", "")
    if not isinstance(description, str):
        description = ""
    return NamedFilter(name=path.stem, query=query.strip(), description=description)
