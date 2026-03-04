"""Tests for fleetroll/notes.py — core notes module."""

from __future__ import annotations

from pathlib import Path

from fleetroll.notes import append_note, append_note_clear, iter_notes, load_latest_notes


class TestAppendNote:
    """Tests for append_note function."""

    def test_creates_file_and_returns_record(self, tmp_dir: Path) -> None:
        """append_note creates the file and returns the appended record."""
        path = tmp_dir / "notes.jsonl"
        record = append_note(path, host="host1.example.com", note="test note", actor="testuser")
        assert path.exists()
        assert record["action"] == "host.note_add"
        assert record["host"] == "host1.example.com"
        assert record["note"] == "test note"
        assert record["actor"] == "testuser"
        assert "ts" in record

    def test_creates_parent_dirs(self, tmp_dir: Path) -> None:
        """append_note creates parent directories if they don't exist."""
        path = tmp_dir / "data" / "subdir" / "notes.jsonl"
        append_note(path, host="host1.example.com", note="hi", actor="u")
        assert path.exists()

    def test_appends_multiple_records(self, tmp_dir: Path) -> None:
        """append_note appends multiple records without overwriting."""
        path = tmp_dir / "notes.jsonl"
        append_note(path, host="host1.example.com", note="first note", actor="user1")
        append_note(path, host="host2.example.com", note="second note", actor="user2")
        lines = path.read_text().splitlines()
        assert len(lines) == 2

    def test_infers_actor_when_not_provided(self, tmp_dir: Path, monkeypatch) -> None:
        """append_note infers actor from environment when not provided."""
        monkeypatch.setenv("USER", "envuser")
        monkeypatch.delenv("FLEETROLL_ACTOR", raising=False)
        monkeypatch.delenv("SUDO_USER", raising=False)
        path = tmp_dir / "notes.jsonl"
        record = append_note(path, host="host1.example.com", note="test")
        assert record["actor"] == "envuser"

    def test_record_has_sorted_keys(self, tmp_dir: Path) -> None:
        """append_note writes records with sorted keys (alphabetical)."""
        import json

        path = tmp_dir / "notes.jsonl"
        append_note(path, host="host1.example.com", note="hello", actor="u")
        line = path.read_text().strip()
        record = json.loads(line)
        keys = list(record.keys())
        assert keys == sorted(keys)


class TestAppendNoteClear:
    """Tests for append_note_clear function."""

    def test_creates_correct_record(self, tmp_dir: Path) -> None:
        """append_note_clear creates a host.note_clear record."""
        path = tmp_dir / "notes.jsonl"
        record = append_note_clear(path, host="host1.example.com", actor="testuser")
        assert record["action"] == "host.note_clear"
        assert record["host"] == "host1.example.com"
        assert record["actor"] == "testuser"
        assert "ts" in record
        assert "reason" not in record

    def test_includes_reason_when_provided(self, tmp_dir: Path) -> None:
        """append_note_clear includes reason field when provided."""
        path = tmp_dir / "notes.jsonl"
        record = append_note_clear(path, host="host1.example.com", actor="u", reason="resolved")
        assert record["reason"] == "resolved"

    def test_omits_reason_when_none(self, tmp_dir: Path) -> None:
        """append_note_clear omits reason key when reason is None."""
        path = tmp_dir / "notes.jsonl"
        record = append_note_clear(path, host="host1.example.com", actor="u")
        assert "reason" not in record


class TestIterNotes:
    """Tests for iter_notes function."""

    def test_returns_empty_for_missing_file(self, tmp_dir: Path) -> None:
        """iter_notes returns nothing for a missing file."""
        path = tmp_dir / "missing.jsonl"
        records = list(iter_notes(path))
        assert records == []

    def test_yields_all_note_records(self, tmp_dir: Path) -> None:
        """iter_notes yields all host.note_add records."""
        path = tmp_dir / "notes.jsonl"
        append_note(path, host="h1.example.com", note="note1", actor="u")
        append_note(path, host="h2.example.com", note="note2", actor="u")
        records = list(iter_notes(path))
        assert len(records) == 2

    def test_filters_by_host(self, tmp_dir: Path) -> None:
        """iter_notes filters records by host when host is provided."""
        path = tmp_dir / "notes.jsonl"
        append_note(path, host="h1.example.com", note="note for h1", actor="u")
        append_note(path, host="h2.example.com", note="note for h2", actor="u")
        records = list(iter_notes(path, host="h1.example.com"))
        assert len(records) == 1
        assert records[0]["host"] == "h1.example.com"

    def test_skips_non_note_records(self, tmp_dir: Path) -> None:
        """iter_notes skips records with other action types."""
        import json

        path = tmp_dir / "notes.jsonl"
        # Write a non-note record
        with path.open("w") as f:
            f.write(json.dumps({"action": "host.audit", "host": "h1.example.com"}) + "\n")
        append_note(path, host="h1.example.com", note="real note", actor="u")
        records = list(iter_notes(path))
        assert len(records) == 1
        assert records[0]["action"] == "host.note_add"

    def test_yields_note_clear_records(self, tmp_dir: Path) -> None:
        """iter_notes yields host.note_clear records."""
        path = tmp_dir / "notes.jsonl"
        append_note(path, host="h1.example.com", note="a note", actor="u")
        append_note_clear(path, host="h1.example.com", actor="u")
        records = list(iter_notes(path))
        assert len(records) == 2
        actions = [r["action"] for r in records]
        assert "host.note_add" in actions
        assert "host.note_clear" in actions


class TestLoadLatestNotes:
    """Tests for load_latest_notes function."""

    def test_returns_empty_for_missing_file(self, tmp_dir: Path) -> None:
        """load_latest_notes returns empty dict for missing file."""
        path = tmp_dir / "missing.jsonl"
        result = load_latest_notes(path)
        assert result == {}

    def test_single_note_no_count_prefix(self, tmp_dir: Path) -> None:
        """load_latest_notes returns plain text when host has only one note."""
        path = tmp_dir / "notes.jsonl"
        append_note(path, host="h1.example.com", note="only note", actor="u")
        result = load_latest_notes(path)
        assert result["h1.example.com"] == "only note"

    def test_multiple_notes_count_prefix(self, tmp_dir: Path) -> None:
        """load_latest_notes returns count prefix when host has multiple notes."""
        path = tmp_dir / "notes.jsonl"
        append_note(path, host="h1.example.com", note="first note", actor="u")
        append_note(path, host="h1.example.com", note="second note", actor="u")
        append_note(path, host="h1.example.com", note="third note", actor="u")
        result = load_latest_notes(path)
        assert result["h1.example.com"] == "(3) third note"

    def test_returns_latest_note_per_host(self, tmp_dir: Path) -> None:
        """load_latest_notes returns the most recent note per host."""
        path = tmp_dir / "notes.jsonl"
        append_note(path, host="h1.example.com", note="old note", actor="u")
        append_note(path, host="h1.example.com", note="new note", actor="u")
        result = load_latest_notes(path)
        assert "new note" in result["h1.example.com"]

    def test_multiple_hosts(self, tmp_dir: Path) -> None:
        """load_latest_notes handles multiple hosts independently."""
        path = tmp_dir / "notes.jsonl"
        append_note(path, host="h1.example.com", note="note for h1", actor="u")
        append_note(path, host="h2.example.com", note="note for h2", actor="u")
        append_note(path, host="h2.example.com", note="second for h2", actor="u")
        result = load_latest_notes(path)
        assert result["h1.example.com"] == "note for h1"
        assert result["h2.example.com"] == "(2) second for h2"

    def test_clear_removes_host_from_result(self, tmp_dir: Path) -> None:
        """load_latest_notes excludes host after a clear tombstone with no subsequent notes."""
        path = tmp_dir / "notes.jsonl"
        append_note(path, host="h1.example.com", note="a note", actor="u")
        append_note_clear(path, host="h1.example.com", actor="u")
        result = load_latest_notes(path)
        assert "h1.example.com" not in result

    def test_clear_then_new_note_only_counts_post_clear(self, tmp_dir: Path) -> None:
        """load_latest_notes only counts notes after the last clear tombstone."""
        path = tmp_dir / "notes.jsonl"
        append_note(path, host="h1.example.com", note="old note 1", actor="u")
        append_note(path, host="h1.example.com", note="old note 2", actor="u")
        append_note_clear(path, host="h1.example.com", actor="u")
        append_note(path, host="h1.example.com", note="new note", actor="u")
        result = load_latest_notes(path)
        assert result["h1.example.com"] == "new note"

    def test_clear_only_affects_target_host(self, tmp_dir: Path) -> None:
        """load_latest_notes clear tombstone does not affect other hosts."""
        path = tmp_dir / "notes.jsonl"
        append_note(path, host="h1.example.com", note="note for h1", actor="u")
        append_note(path, host="h2.example.com", note="note for h2", actor="u")
        append_note_clear(path, host="h1.example.com", actor="u")
        result = load_latest_notes(path)
        assert "h1.example.com" not in result
        assert result["h2.example.com"] == "note for h2"
