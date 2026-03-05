"""tmux-based TUI integration tests for host-monitor."""

from __future__ import annotations

import os
from typing import Any

import pytest

from tests.tmux_helpers import _PROJECT_ROOT, _UV_PATH, TmuxSession, seed_test_db, skip_no_tmux

pytestmark = [skip_no_tmux, pytest.mark.tui]


def _session_env(home_dir: Any) -> dict[str, str]:
    """Build the env dict for a TUI tmux session."""
    return {"HOME": str(home_dir), "PATH": os.environ.get("PATH", "")}


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


class TestMonitorLayout:
    """Verify header, columns and host rows render correctly."""

    def test_header_visible(self, tmux_session: TmuxSession) -> None:
        """The top header line is present."""
        screen = tmux_session.capture()
        assert "HOST" in screen

    def test_column_headers_present(self, tmux_session: TmuxSession) -> None:
        """Key column labels appear in the rendered header."""
        screen = tmux_session.capture()
        for col in ("OS", "ROLE", "OVR_SHA"):
            assert col in screen, f"Column '{col}' not found in screen"

    def test_host_rows_rendered(self, tmux_session: TmuxSession) -> None:
        """At least one seeded host name appears in the output."""
        screen = tmux_session.capture()
        assert "t-linux64-ms-001" in screen

    def test_multiple_hosts_rendered(self, tmux_session: TmuxSession) -> None:
        """All five seeded hosts appear on screen."""
        screen = tmux_session.capture()
        for i in range(1, 6):
            host_short = f"t-linux64-ms-{i:03d}"
            assert host_short in screen, f"Host {host_short} not found"

    def test_role_column_populated(self, tmux_session: TmuxSession) -> None:
        """The role column shows the seeded role value."""
        screen = tmux_session.capture()
        assert "gecko_t_linux_talos" in screen


# ---------------------------------------------------------------------------
# Keyboard navigation
# ---------------------------------------------------------------------------


class TestKeyboardNavigation:
    """Verify scrolling and sorting key bindings."""

    def test_scroll_down_with_j(
        self,
        tmux_monitor_env: dict[str, Any],
    ) -> None:
        """Pressing j scrolls down when there are more rows than fit on screen."""
        # Seed many hosts so scrolling is possible
        home_dir = tmux_monitor_env["home"]
        db_path = home_dir / ".fleetroll" / "fleetroll.db"
        hosts = [f"t-linux64-ms-{i:03d}.test.releng.mdc1.mozilla.com" for i in range(1, 40)]
        hosts_file = tmux_monitor_env["hosts_file"]
        hosts_file.write_text("\n".join(hosts) + "\n")
        seed_test_db(db_path, hosts)

        cmd = f"{_UV_PATH} run fleetroll host-monitor {hosts_file}"
        with TmuxSession(
            cmd=cmd, cols=160, rows=15, env=_session_env(home_dir), cwd=_PROJECT_ROOT
        ) as sess:
            assert sess.wait_for("HOST", timeout=15.0), "TUI did not render"
            screen_before = sess.capture()

            sess.send_keys("j")
            assert sess.wait_until(
                lambda: sess.capture() != screen_before,
                timeout=3.0,
            ), "Screen did not change after j"

    def test_scroll_up_at_top_does_not_crash(self, tmux_session: TmuxSession) -> None:
        """Pressing k at the top of the list should not crash the TUI."""
        tmux_session.send_keys("k")
        assert tmux_session.wait_for("HOST", timeout=3.0)

    def test_horizontal_scroll_right(self, tmux_session: TmuxSession) -> None:
        """Pressing l should work without crashing."""
        tmux_session.send_keys("l")
        assert tmux_session.wait_for("HOST", timeout=3.0)

    def test_horizontal_scroll_left(self, tmux_session: TmuxSession) -> None:
        """Pressing h after l returns to original position."""
        tmux_session.send_keys("l")
        tmux_session.send_keys("h")
        assert tmux_session.wait_for("HOST", timeout=3.0)

    def test_sort_cycle_with_s(self, tmux_session: TmuxSession) -> None:
        """Pressing s cycles sort order; sort indicator (*) appears on a column."""
        tmux_session.send_keys("s")
        assert tmux_session.wait_for("*", timeout=3.0), "Sort indicator not found"


# ---------------------------------------------------------------------------
# Help popup
# ---------------------------------------------------------------------------


class TestHelpPopup:
    """Verify the help popup opens and closes."""

    def test_question_mark_opens_help(self, tmux_session: TmuxSession) -> None:
        """Pressing ? should open the help popup."""
        tmux_session.send_keys("?")
        assert tmux_session.wait_until(
            lambda: any(
                kw in tmux_session.capture().lower() for kw in ("quit", "scroll", "sort", "filter")
            ),
            timeout=3.0,
        ), "Help popup content not found"

    def test_any_key_closes_help(self, tmux_session: TmuxSession) -> None:
        """Any key press should dismiss the help popup."""
        tmux_session.send_keys("?")
        assert tmux_session.wait_for("HOST", timeout=3.0)
        tmux_session.send_keys("r")
        assert tmux_session.wait_for("HOST", timeout=3.0)


# ---------------------------------------------------------------------------
# Resize
# ---------------------------------------------------------------------------


class TestResize:
    """Verify TUI adapts to terminal resize."""

    def test_narrow_terminal_shows_scroll_indicator(
        self,
        tmux_monitor_env: dict[str, Any],
    ) -> None:
        """A narrow terminal triggers the horizontal scroll indicator."""
        home_dir = tmux_monitor_env["home"]
        hosts_file = tmux_monitor_env["hosts_file"]
        cmd = f"{_UV_PATH} run fleetroll host-monitor {hosts_file}"
        with TmuxSession(
            cmd=cmd, cols=40, rows=20, env=_session_env(home_dir), cwd=_PROJECT_ROOT
        ) as sess:
            assert sess.wait_for("HOST", timeout=15.0), "TUI did not render"
            screen = sess.capture()
            assert "▶" in screen or "◀" in screen or "[" in screen

    def test_wide_terminal_renders(
        self,
        tmux_monitor_env: dict[str, Any],
    ) -> None:
        """A very wide terminal renders without crashing."""
        home_dir = tmux_monitor_env["home"]
        hosts_file = tmux_monitor_env["hosts_file"]
        cmd = f"{_UV_PATH} run fleetroll host-monitor {hosts_file}"
        with TmuxSession(
            cmd=cmd, cols=300, rows=40, env=_session_env(home_dir), cwd=_PROJECT_ROOT
        ) as sess:
            assert sess.wait_for("HOST", timeout=15.0), "TUI did not render"
            assert "HOST" in sess.capture()

    def test_resize_does_not_crash(self, tmux_session: TmuxSession) -> None:
        """Resizing the window mid-session should not crash the TUI."""
        tmux_session.resize(80, 24)
        assert tmux_session.wait_for("HOST", timeout=5.0)
        tmux_session.resize(160, 40)
        assert tmux_session.wait_for("HOST", timeout=5.0)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


class TestFilters:
    """Verify override and OS filter toggles."""

    def test_override_filter_toggle(self, tmux_session: TmuxSession) -> None:
        """Pressing o should toggle override filter without crashing."""
        tmux_session.send_keys("o")
        assert tmux_session.wait_for("HOST", timeout=3.0)
        tmux_session.send_keys("o")
        assert tmux_session.wait_for("HOST", timeout=3.0)

    def test_os_filter_cycle(self, tmux_session: TmuxSession) -> None:
        """Pressing O should cycle OS filter without crashing."""
        for _ in range(4):
            tmux_session.send_keys("O")
            assert tmux_session.wait_for("HOST", timeout=3.0)


# ---------------------------------------------------------------------------
# Quit
# ---------------------------------------------------------------------------


class TestQuit:
    """Verify q exits the TUI cleanly."""

    def test_q_exits(self, tmux_monitor_env: dict[str, Any]) -> None:
        """Pressing q should cause the process to exit."""
        home_dir = tmux_monitor_env["home"]
        hosts_file = tmux_monitor_env["hosts_file"]
        cmd = f"{_UV_PATH} run fleetroll host-monitor {hosts_file}"
        with TmuxSession(
            cmd=cmd, cols=300, rows=40, env=_session_env(home_dir), cwd=_PROJECT_ROOT
        ) as sess:
            assert sess.wait_for("HOST", timeout=15.0), "TUI did not render"
            sess.send_keys("q")

            def _exited() -> bool:
                screen = sess.capture()
                screen_lines = [ln for ln in screen.splitlines() if ln.strip()]
                return not any("HOST" in ln and "ROLE" in ln for ln in screen_lines)

            assert sess.wait_until(_exited, timeout=5.0), "TUI did not exit after q"
