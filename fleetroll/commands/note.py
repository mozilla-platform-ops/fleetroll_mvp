"""Note-add and show-notes command implementations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..notes import append_note, default_notes_path, iter_notes
from ..utils import infer_actor


def cmd_note_add(
    hostname: str,
    note_text: str,
    *,
    notes_file: str | None = None,
    json_output: bool = False,
) -> None:
    """Add a note for a host.

    Args:
        hostname: Hostname to annotate
        note_text: Note text
        notes_file: Path to notes file (default: data/notes.jsonl)
        json_output: If True, emit JSON output
    """
    path = Path(notes_file) if notes_file else default_notes_path()
    actor = infer_actor()
    record = append_note(path, host=hostname, note=note_text, actor=actor)
    if json_output:
        print(json.dumps(record, sort_keys=True))
    else:
        print(f"Note added for {hostname}: {note_text!r}")


def cmd_show_notes(
    hostname: str,
    *,
    limit: int | None = None,
    notes_file: str | None = None,
    json_output: bool = False,
) -> None:
    """Show notes for a host.

    Args:
        hostname: Hostname to show notes for
        limit: Maximum number of notes to show (most recent first)
        notes_file: Path to notes file (default: data/notes.jsonl)
        json_output: If True, emit JSON output (one record per line)
    """
    path = Path(notes_file) if notes_file else default_notes_path()
    records: list[dict[str, Any]] = list(iter_notes(path, host=hostname))

    if limit is not None:
        records = records[-limit:]

    if not records:
        if json_output:
            print(json.dumps([]))
        else:
            print(f"No notes for {hostname}")
        return

    if json_output:
        print(json.dumps(records, sort_keys=True))
    else:
        for record in records:
            ts = record.get("ts", "?")
            note = record.get("note", "")
            actor = record.get("actor", "?")
            print(f"[{ts}] ({actor}) {note}")
