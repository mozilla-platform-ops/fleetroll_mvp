"""Unit tests for filter_history helpers."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from fleetroll.commands.monitor.filter_history import (
    dedupe_append,
    load_filter_history,
    save_filter_history,
)
from fleetroll.constants import FILTER_HISTORY_MAX


def test_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "filter_history"
    entries = ["foo", "bar", "baz"]
    save_filter_history(p, entries)
    assert load_filter_history(p) == entries


def test_load_missing_file(tmp_path: Path) -> None:
    assert load_filter_history(tmp_path / "nonexistent") == []


def test_load_ignores_blank_lines(tmp_path: Path) -> None:
    p = tmp_path / "filter_history"
    p.write_text("foo\n\nbar\n\n", encoding="utf-8")
    assert load_filter_history(p) == ["foo", "bar"]


def test_save_truncates_to_max(tmp_path: Path) -> None:
    p = tmp_path / "filter_history"
    entries = [str(i) for i in range(FILTER_HISTORY_MAX + 50)]
    save_filter_history(p, entries)
    loaded = load_filter_history(p)
    assert len(loaded) == FILTER_HISTORY_MAX
    assert loaded[0] == str(50)
    assert loaded[-1] == str(FILTER_HISTORY_MAX + 49)


def test_load_truncates_to_max(tmp_path: Path) -> None:
    p = tmp_path / "filter_history"
    lines = "\n".join(str(i) for i in range(FILTER_HISTORY_MAX + 20)) + "\n"
    p.write_text(lines, encoding="utf-8")
    loaded = load_filter_history(p)
    assert len(loaded) == FILTER_HISTORY_MAX


def test_dedupe_append_moves_existing_to_end() -> None:
    hist: list[str] = ["foo", "bar", "baz"]
    dedupe_append(hist, "foo")
    assert hist == ["bar", "baz", "foo"]


def test_dedupe_append_new_entry() -> None:
    hist: list[str] = ["foo", "bar"]
    dedupe_append(hist, "qux")
    assert hist == ["foo", "bar", "qux"]


def test_dedupe_append_enforces_max() -> None:
    hist = list(map(str, range(FILTER_HISTORY_MAX)))
    dedupe_append(hist, "new")
    assert len(hist) == FILTER_HISTORY_MAX
    assert hist[-1] == "new"
    assert "0" not in hist


@pytest.mark.skipif(
    not hasattr(stat, "S_ISVTX"),
    reason="permission test only meaningful on POSIX",
)
def test_save_unwritable_path_does_not_raise(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o444)
    try:
        save_filter_history(locked / "filter_history", ["foo"])
    finally:
        locked.chmod(0o755)
