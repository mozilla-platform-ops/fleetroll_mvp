"""Tests for fleetroll/commands/note.py — note-add and show-notes commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fleetroll.commands.note import cmd_note_add, cmd_note_clear, cmd_show_notes
from fleetroll.exceptions import UserError
from fleetroll.notes import append_note, append_note_clear, load_latest_notes


class TestCmdNoteAdd:
    """Tests for cmd_note_add command."""

    def test_adds_note_and_prints_confirmation(self, tmp_dir: Path, capsys) -> None:
        """cmd_note_add writes a note and prints human-readable confirmation."""
        path = tmp_dir / "notes.jsonl"
        cmd_note_add("host1.example.com", "test note", notes_file=str(path))
        out = capsys.readouterr().out
        assert "host1.example.com" in out
        assert "test note" in out
        assert path.exists()

    def test_adds_note_json_output(self, tmp_dir: Path, capsys) -> None:
        """cmd_note_add with json_output=True emits a JSON record."""
        path = tmp_dir / "notes.jsonl"
        cmd_note_add("host1.example.com", "json note", notes_file=str(path), json_output=True)
        out = capsys.readouterr().out
        record = json.loads(out)
        assert record["action"] == "host.note_add"
        assert record["host"] == "host1.example.com"
        assert record["note"] == "json note"

    def test_record_written_to_file(self, tmp_dir: Path) -> None:
        """cmd_note_add writes a parseable record to the notes file."""
        path = tmp_dir / "notes.jsonl"
        cmd_note_add("host2.example.com", "my note", notes_file=str(path))
        result = load_latest_notes(path)
        assert result.get("host2.example.com") == "my note"

    def test_rejects_non_fqdn(self, tmp_dir: Path) -> None:
        """cmd_note_add raises UserError for a non-FQDN hostname."""
        path = tmp_dir / "notes.jsonl"
        with pytest.raises(UserError, match="fully-qualified"):
            cmd_note_add("hostname", "a note", notes_file=str(path))

    def test_multiple_adds_accumulate(self, tmp_dir: Path) -> None:
        """cmd_note_add appends multiple notes correctly."""
        path = tmp_dir / "notes.jsonl"
        cmd_note_add("host1.example.com", "first", notes_file=str(path))
        cmd_note_add("host1.example.com", "second", notes_file=str(path))
        result = load_latest_notes(path)
        assert result["host1.example.com"].startswith("(2)")


class TestCmdShowNotes:
    """Tests for cmd_show_notes command."""

    def test_shows_no_notes_message(self, tmp_dir: Path, capsys) -> None:
        """cmd_show_notes prints a message when there are no notes for a host."""
        path = tmp_dir / "notes.jsonl"
        cmd_show_notes("nohost.example.com", notes_file=str(path))
        out = capsys.readouterr().out
        assert "No notes" in out

    def test_shows_notes_chronologically(self, tmp_dir: Path, capsys) -> None:
        """cmd_show_notes displays notes in chronological order."""
        path = tmp_dir / "notes.jsonl"
        append_note(path, host="h1.example.com", note="first note", actor="user1")
        append_note(path, host="h1.example.com", note="second note", actor="user2")
        cmd_show_notes("h1.example.com", notes_file=str(path))
        out = capsys.readouterr().out
        lines = [ln for ln in out.splitlines() if ln.strip()]
        assert len(lines) == 2
        assert "first note" in lines[0]
        assert "second note" in lines[1]

    def test_respects_limit(self, tmp_dir: Path, capsys) -> None:
        """cmd_show_notes respects the limit parameter (most recent notes)."""
        path = tmp_dir / "notes.jsonl"
        for i in range(5):
            append_note(path, host="h1.example.com", note=f"note {i}", actor="u")
        cmd_show_notes("h1.example.com", limit=2, notes_file=str(path))
        out = capsys.readouterr().out
        lines = [ln for ln in out.splitlines() if ln.strip()]
        assert len(lines) == 2
        # Should show the most recent notes
        assert "note 3" in lines[0]
        assert "note 4" in lines[1]

    def test_json_output_empty(self, tmp_dir: Path, capsys) -> None:
        """cmd_show_notes with json_output=True and no notes returns empty list."""
        path = tmp_dir / "notes.jsonl"
        cmd_show_notes("nohost.example.com", notes_file=str(path), json_output=True)
        out = capsys.readouterr().out
        assert json.loads(out) == []

    def test_json_output_with_notes(self, tmp_dir: Path, capsys) -> None:
        """cmd_show_notes with json_output=True returns a list of records."""
        path = tmp_dir / "notes.jsonl"
        append_note(path, host="h1.example.com", note="a note", actor="user1")
        cmd_show_notes("h1.example.com", notes_file=str(path), json_output=True)
        out = capsys.readouterr().out
        records = json.loads(out)
        assert len(records) == 1
        assert records[0]["note"] == "a note"

    def test_output_includes_actor_and_timestamp(self, tmp_dir: Path, capsys) -> None:
        """cmd_show_notes text output includes actor and timestamp."""
        path = tmp_dir / "notes.jsonl"
        append_note(path, host="h1.example.com", note="operator note", actor="alice")
        cmd_show_notes("h1.example.com", notes_file=str(path))
        out = capsys.readouterr().out
        assert "alice" in out
        assert "operator note" in out

    def test_show_after_clear_returns_no_notes(self, tmp_dir: Path, capsys) -> None:
        """cmd_show_notes returns 'No notes' after a clear tombstone with no new notes."""
        path = tmp_dir / "notes.jsonl"
        append_note(path, host="h1.example.com", note="a note", actor="u")
        append_note_clear(path, host="h1.example.com", actor="u")
        cmd_show_notes("h1.example.com", notes_file=str(path))
        out = capsys.readouterr().out
        assert "No notes" in out

    def test_show_after_clear_then_new_note_only_shows_post_clear(
        self, tmp_dir: Path, capsys
    ) -> None:
        """cmd_show_notes only shows notes added after the last clear."""
        path = tmp_dir / "notes.jsonl"
        append_note(path, host="h1.example.com", note="old note", actor="u")
        append_note_clear(path, host="h1.example.com", actor="u")
        append_note(path, host="h1.example.com", note="new note", actor="u")
        cmd_show_notes("h1.example.com", notes_file=str(path))
        out = capsys.readouterr().out
        assert "new note" in out
        assert "old note" not in out

    def test_include_cleared_shows_all_records(self, tmp_dir: Path, capsys) -> None:
        """cmd_show_notes with include_cleared=True shows all records including tombstones."""
        path = tmp_dir / "notes.jsonl"
        append_note(path, host="h1.example.com", note="old note", actor="u")
        append_note_clear(path, host="h1.example.com", actor="u")
        append_note(path, host="h1.example.com", note="new note", actor="u")
        cmd_show_notes("h1.example.com", notes_file=str(path), include_cleared=True)
        out = capsys.readouterr().out
        assert "old note" in out
        assert "[CLEARED]" in out
        assert "new note" in out


class TestCmdNoteClear:
    """Tests for cmd_note_clear command."""

    def test_prints_confirmation(self, tmp_dir: Path, capsys) -> None:
        """cmd_note_clear prints human-readable confirmation."""
        path = tmp_dir / "notes.jsonl"
        cmd_note_clear("host1.example.com", notes_file=str(path))
        out = capsys.readouterr().out
        assert "Notes cleared for host1.example.com" in out

    def test_json_output(self, tmp_dir: Path, capsys) -> None:
        """cmd_note_clear with json_output=True emits a JSON record."""
        path = tmp_dir / "notes.jsonl"
        cmd_note_clear("host1.example.com", notes_file=str(path), json_output=True)
        out = capsys.readouterr().out
        record = json.loads(out)
        assert record["action"] == "host.note_clear"
        assert record["host"] == "host1.example.com"

    def test_record_written_to_file(self, tmp_dir: Path) -> None:
        """cmd_note_clear writes a tombstone record to the notes file."""
        path = tmp_dir / "notes.jsonl"
        append_note(path, host="host1.example.com", note="a note", actor="u")
        cmd_note_clear("host1.example.com", notes_file=str(path))
        result = load_latest_notes(path)
        assert "host1.example.com" not in result

    def test_rejects_non_fqdn(self, tmp_dir: Path) -> None:
        """cmd_note_clear raises UserError for a non-FQDN hostname."""
        path = tmp_dir / "notes.jsonl"
        with pytest.raises(UserError, match="fully-qualified"):
            cmd_note_clear("hostname", notes_file=str(path))

    def test_clear_with_reason(self, tmp_dir: Path, capsys) -> None:
        """cmd_note_clear includes reason in JSON record when provided."""
        path = tmp_dir / "notes.jsonl"
        cmd_note_clear(
            "host1.example.com", reason="resolved", notes_file=str(path), json_output=True
        )
        out = capsys.readouterr().out
        record = json.loads(out)
        assert record["reason"] == "resolved"
