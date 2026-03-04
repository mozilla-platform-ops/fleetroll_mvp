"""FleetRoll host notes module."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .audit import append_jsonl, iter_audit_records
from .constants import AUDIT_DIR_NAME, DATA_DIR_NAME, NOTES_FILE_NAME
from .utils import infer_actor, utc_now_iso


def _find_project_root() -> Path | None:
    """Walk up from this file's location to find the project root."""
    current = Path(__file__).resolve().parent
    for _ in range(10):
        if (current / "pyproject.toml").exists() or (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def default_notes_path() -> Path:
    """Return default path for notes file.

    Looks for data/notes.jsonl relative to the project root (pyproject.toml or .git),
    falling back to ~/.fleetroll/notes.jsonl.
    """
    root = _find_project_root()
    if root is not None:
        return root / DATA_DIR_NAME / NOTES_FILE_NAME
    home = Path(os.path.expanduser("~"))
    return home / AUDIT_DIR_NAME / NOTES_FILE_NAME


def append_note(path: Path, *, host: str, note: str, actor: str | None = None) -> dict[str, Any]:
    """Append a note record to the notes file and return it.

    Args:
        path: Path to the notes JSONL file
        host: Hostname to annotate
        note: Note text
        actor: Actor performing the annotation (inferred if None)

    Returns:
        The record dict that was appended
    """
    if actor is None:
        actor = infer_actor()
    record: dict[str, Any] = {
        "action": "host.note_add",
        "actor": actor,
        "host": host,
        "note": note,
        "ts": utc_now_iso(),
    }
    append_jsonl(path, record)
    return record


def iter_notes(path: Path, *, host: str | None = None) -> Iterable[dict[str, Any]]:
    """Yield note records from the notes file, optionally filtered by host.

    Args:
        path: Path to the notes JSONL file
        host: If provided, only yield records for this host

    Yields:
        Note record dicts
    """
    for record in iter_audit_records(path):
        if record.get("action") != "host.note_add":
            continue
        if host is not None and record.get("host") != host:
            continue
        yield record


def load_latest_notes(path: Path) -> dict[str, str]:
    """Return the latest note per host as a display string.

    Scans all records and returns the most recent note per host.
    Format: "(N) latest note text" when N > 1, "latest note text" when N == 1.

    Args:
        path: Path to the notes JSONL file

    Returns:
        Dict mapping hostname to display string
    """
    counts: dict[str, int] = {}
    latest_text: dict[str, str] = {}
    for record in iter_notes(path):
        host = record.get("host")
        note_text = record.get("note")
        if not host or not note_text:
            continue
        counts[host] = counts.get(host, 0) + 1
        latest_text[host] = note_text

    result: dict[str, str] = {}
    for host, text in latest_text.items():
        n = counts[host]
        if n > 1:
            result[host] = f"({n}) {text}"
        else:
            result[host] = text
    return result
