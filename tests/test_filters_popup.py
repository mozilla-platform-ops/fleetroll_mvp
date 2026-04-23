"""Tests for filters picker popup pure helpers."""

from __future__ import annotations

from fleetroll.commands.monitor.filters_popup import (
    TAB_RECENT,
    TAB_SAVED,
    FiltersPopupState,
    PopupRow,
    build_recent_rows,
    build_saved_rows,
    compute_popup_viewport,
    filter_rows,
)
from fleetroll.commands.monitor.named_filters import NamedFilter


class TestComputePopupViewport:
    def test_no_scroll_needed_keeps_start(self) -> None:
        assert (
            compute_popup_viewport(selected=2, viewport_start=0, visible_rows=5, total_rows=3) == 0
        )

    def test_cursor_above_viewport_scrolls_up(self) -> None:
        assert (
            compute_popup_viewport(selected=1, viewport_start=3, visible_rows=4, total_rows=20) == 1
        )

    def test_cursor_below_viewport_scrolls_down(self) -> None:
        # visible_rows=4, selected=10 → start must be 7 (so selected == start+3)
        assert (
            compute_popup_viewport(selected=10, viewport_start=0, visible_rows=4, total_rows=20)
            == 7
        )

    def test_clamps_to_max_start(self) -> None:
        # total=10, visible=4 → max_start=6
        assert (
            compute_popup_viewport(selected=5, viewport_start=99, visible_rows=4, total_rows=10)
            == 5
        )

    def test_zero_total_returns_zero(self) -> None:
        assert (
            compute_popup_viewport(selected=0, viewport_start=5, visible_rows=4, total_rows=0) == 0
        )

    def test_zero_visible_returns_zero(self) -> None:
        assert (
            compute_popup_viewport(selected=3, viewport_start=1, visible_rows=0, total_rows=10) == 0
        )


class TestFilterRows:
    def _rows(self) -> list[PopupRow]:
        return [
            PopupRow(label="prod-talos", query="os=L role~talos"),
            PopupRow(label="linux-workers", query="os=L role~builder"),
            PopupRow(label="staging", query="env=staging"),
        ]

    def test_empty_search_returns_all(self) -> None:
        rows = self._rows()
        assert filter_rows(rows, "") == rows

    def test_substring_match_on_label(self) -> None:
        out = filter_rows(self._rows(), "prod")
        assert [r.label for r in out] == ["prod-talos"]

    def test_substring_match_on_query(self) -> None:
        out = filter_rows(self._rows(), "talos")
        assert [r.label for r in out] == ["prod-talos"]

    def test_case_insensitive(self) -> None:
        out = filter_rows(self._rows(), "PROD")
        assert [r.label for r in out] == ["prod-talos"]

    def test_no_match_returns_empty(self) -> None:
        assert filter_rows(self._rows(), "zzzz") == []


class TestBuildRows:
    def test_build_saved_rows(self) -> None:
        filters = [
            NamedFilter(name="a", query="os=L"),
            NamedFilter(name="b", query="os=M"),
        ]
        rows = build_saved_rows(filters)
        assert [(r.label, r.query) for r in rows] == [("a", "os=L"), ("b", "os=M")]

    def test_build_recent_rows_reversed(self) -> None:
        # history is oldest→newest; most recent should be first
        rows = build_recent_rows(["old", "mid", "newest"])
        assert [r.query for r in rows] == ["newest", "mid", "old"]

    def test_build_recent_rows_empty_history(self) -> None:
        assert build_recent_rows([]) == []

    def test_build_recent_rows_skips_empty(self) -> None:
        rows = build_recent_rows(["", "x", ""])
        assert [r.query for r in rows] == ["x"]


class TestPopupStatePerTabCursor:
    def test_switching_tabs_preserves_per_tab_cursor(self) -> None:
        state = FiltersPopupState()
        state.active_tab = TAB_SAVED
        state.tab_state().cursor = 2
        state.active_tab = TAB_RECENT
        state.tab_state().cursor = 5
        state.active_tab = TAB_SAVED
        assert state.tab_state().cursor == 2
        state.active_tab = TAB_RECENT
        assert state.tab_state().cursor == 5
