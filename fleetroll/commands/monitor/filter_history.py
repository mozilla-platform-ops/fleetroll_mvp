from __future__ import annotations

import contextlib
from pathlib import Path

from fleetroll.commands.monitor.query import migrate_legacy_empty_syntax
from fleetroll.constants import AUDIT_DIR_NAME, FILTER_HISTORY_FILE_NAME, FILTER_HISTORY_MAX


def filter_history_path() -> Path:
    return Path.home() / AUDIT_DIR_NAME / FILTER_HISTORY_FILE_NAME


def load_filter_history(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries = [migrate_legacy_empty_syntax(line) for line in lines if line.strip()]
    return entries[-FILTER_HISTORY_MAX:]


def save_filter_history(path: Path, history: list[str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(history[-FILTER_HISTORY_MAX:]) + "\n" if history else "",
            encoding="utf-8",
        )
    except OSError:
        pass


def dedupe_append(history: list[str], text: str) -> None:
    """Remove any prior equal entry then append text (LRU-style, in-place)."""
    with contextlib.suppress(ValueError):
        history.remove(text)
    history.append(text)
    if len(history) > FILTER_HISTORY_MAX:
        del history[: len(history) - FILTER_HISTORY_MAX]
