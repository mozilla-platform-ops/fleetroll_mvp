"""Note-add and show-notes command implementations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..notes import append_note, append_note_clear, default_notes_path, iter_notes
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


def cmd_note_clear(
    hostname: str,
    *,
    reason: str | None = None,
    notes_file: str | None = None,
    json_output: bool = False,
) -> None:
    """Clear notes for a host by appending a tombstone record.

    Args:
        hostname: Hostname whose notes are being cleared
        reason: Optional reason for clearing notes
        notes_file: Path to notes file (default: data/notes.jsonl)
        json_output: If True, emit JSON output
    """
    path = Path(notes_file) if notes_file else default_notes_path()
    actor = infer_actor()
    record = append_note_clear(path, host=hostname, actor=actor, reason=reason)
    if json_output:
        print(json.dumps(record, sort_keys=True))
    else:
        print(f"Notes cleared for {hostname}")


def cmd_show_notes(
    hostname: str,
    *,
    limit: int | None = None,
    notes_file: str | None = None,
    json_output: bool = False,
    include_cleared: bool = False,
) -> None:
    """Show notes for a host.

    Args:
        hostname: Hostname to show notes for
        limit: Maximum number of notes to show (most recent first)
        notes_file: Path to notes file (default: data/notes.jsonl)
        json_output: If True, emit JSON output (one record per line)
        include_cleared: If True, show all records including clear tombstones
    """
    path = Path(notes_file) if notes_file else default_notes_path()
    all_records: list[dict[str, Any]] = list(iter_notes(path, host=hostname))

    if include_cleared:
        records = all_records
    else:
        # Find the last clear tombstone and only show records after it
        last_clear_idx = -1
        for i, record in enumerate(all_records):
            if record.get("action") == "host.note_clear":
                last_clear_idx = i
        if last_clear_idx >= 0:
            records = [
                r for r in all_records[last_clear_idx + 1 :] if r.get("action") == "host.note_add"
            ]
        else:
            records = [r for r in all_records if r.get("action") == "host.note_add"]

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
            actor = record.get("actor", "?")
            if record.get("action") == "host.note_clear":
                reason = record.get("reason", "")
                suffix = f" ({reason})" if reason else ""
                print(f"[{ts}] ({actor}) [CLEARED]{suffix}")
            else:
                note = record.get("note", "")
                print(f"[{ts}] ({actor}) {note}")
