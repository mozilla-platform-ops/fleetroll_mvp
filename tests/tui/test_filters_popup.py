"""tmux-based TUI integration tests for the filters picker popup."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fleetroll.commands.monitor.filter_history import record_filter_use

from tests.tmux_helpers import (
    _UV_PATH,
    TmuxSession,
    seed_test_db,
    skip_no_tmux,
)

pytestmark = [skip_no_tmux, pytest.mark.tui]


def _session_env(home_dir: Any) -> dict[str, str]:
    return {
        "HOME": str(home_dir),
        "PATH": os.environ.get("PATH", ""),
        "TERM": os.environ.get("TERM", "xterm-256color"),
    }


def _prepare_workspace(tmp_path: Path, *, filter_yaml: dict[str, str]) -> dict[str, Any]:
    """Set up an isolated workspace with hosts, DB, and configs/filters/*.yaml."""
    home = tmp_path / "home"
    home.mkdir()
    fleetroll_dir = home / ".fleetroll"
    fleetroll_dir.mkdir()
    hosts = [f"t-linux64-ms-{i:03d}.test.releng.mdc1.mozilla.com" for i in range(1, 6)]
    db_path = fleetroll_dir / "fleetroll.db"
    seed_test_db(db_path, hosts)

    hosts_file = tmp_path / "hosts.txt"
    hosts_file.write_text("\n".join(hosts) + "\n")

    filters_dir = tmp_path / "configs" / "filters"
    filters_dir.mkdir(parents=True)
    for name, body in filter_yaml.items():
        (filters_dir / f"{name}.yaml").write_text(body)

    return {
        "home": home,
        "hosts_file": hosts_file,
        "cwd": tmp_path,
    }


def _launch(ws: dict[str, Any]) -> TmuxSession:
    cmd = f"{_UV_PATH} run fleetroll host-monitor {ws['hosts_file']}"
    sess = TmuxSession(
        cmd=cmd,
        cols=160,
        rows=40,
        env=_session_env(ws["home"]),
        cwd=ws["cwd"],
    )
    sess.start()
    if not sess.wait_for("HOST", timeout=15.0):
        out = sess.capture()
        sess.kill()
        pytest.fail(f"monitor did not render:\n{out}")
    return sess


class TestFiltersPopup:
    def test_f_opens_popup(self, tmp_path: Path) -> None:
        ws = _prepare_workspace(
            tmp_path,
            filter_yaml={
                "prod-talos": "query: os=L role~talos\n",
                "linux-all": "query: os=L\n",
            },
        )
        sess = _launch(ws)
        try:
            sess.send_keys("f")
            assert sess.wait_for("Filters", timeout=5.0), sess.capture()
            screen = sess.capture()
            assert "Saved" in screen
            assert "Recent" in screen
            assert "prod-talos" in screen
            assert "linux-all" in screen
        finally:
            sess.kill()

    def test_esc_closes_popup(self, tmp_path: Path) -> None:
        ws = _prepare_workspace(
            tmp_path,
            filter_yaml={"prod-talos": "query: os=L\n"},
        )
        sess = _launch(ws)
        try:
            sess.send_keys("f")
            assert sess.wait_for("prod-talos", timeout=5.0), sess.capture()
            sess.send_keys("Escape")
            assert sess.wait_until(lambda: "prod-talos" not in sess.capture(), timeout=5.0), (
                sess.capture()
            )
        finally:
            sess.kill()

    def test_enter_applies_query(self, tmp_path: Path) -> None:
        ws = _prepare_workspace(
            tmp_path,
            filter_yaml={"linux-only": "query: os=L\n"},
        )
        sess = _launch(ws)
        try:
            sess.send_keys("f")
            assert sess.wait_for("linux-only", timeout=5.0)
            sess.send_keys("Enter")
            # Popup closes and the query shows up in the header.
            assert sess.wait_until(
                lambda: "os=L" in sess.capture() and "Saved" not in sess.capture(),
                timeout=5.0,
            ), sess.capture()
        finally:
            sess.kill()

    def test_typing_narrows_list(self, tmp_path: Path) -> None:
        ws = _prepare_workspace(
            tmp_path,
            filter_yaml={
                "prod-talos": "query: os=L role~talos\n",
                "linux-all": "query: os=L\n",
                "staging": "query: env=staging\n",
            },
        )
        sess = _launch(ws)
        try:
            sess.send_keys("f")
            assert sess.wait_for("prod-talos", timeout=5.0)
            # Type "prod" — only prod-talos should remain
            for ch in "prod":
                sess.send_keys(ch)
            assert sess.wait_until(
                lambda: "linux-all" not in sess.capture() and "prod-talos" in sess.capture(),
                timeout=5.0,
            ), sess.capture()
            # Status shows find: prod
            assert "find: prod" in sess.capture()
        finally:
            sess.kill()

    def test_left_arrow_switches_to_empty_recent(self, tmp_path: Path) -> None:
        ws = _prepare_workspace(
            tmp_path,
            filter_yaml={"prod-talos": "query: os=L\n"},
        )
        sess = _launch(ws)
        try:
            sess.send_keys("f")
            assert sess.wait_for("[Saved]", timeout=5.0)
            sess.send_keys("Left")
            assert sess.wait_until(
                lambda: "prod-talos" not in sess.capture() and "[Recent]" in sess.capture(),
                timeout=5.0,
            ), sess.capture()
        finally:
            sess.kill()

    def test_recent_names_and_usage_across_sessions(self, tmp_path: Path) -> None:
        ws = _prepare_workspace(
            tmp_path,
            filter_yaml={"oldest-data-first": "query: sort:data:desc,healthy\n"},
        )
        history = ws["home"] / ".fleetroll" / "filter_history"
        history.write_text("sort:data:desc,healthy\nhealthy=n\n")
        record_filter_use(history.with_name("filter_usage.sqlite3"), query="healthy=n")
        sess = _launch(ws)
        try:
            sess.send_keys("f")
            assert sess.wait_for("[Recent]", timeout=5.0), sess.capture()
            screen = sess.capture()
            named = next(line for line in screen.splitlines() if "oldest-data-first" in line)
            unnamed = next(line for line in screen.splitlines() if "healthy=n" in line)
            assert unnamed.index("healthy=n") == named.index("oldest-data-first")
            assert "Ad hoc" not in screen
            for char in "first":
                sess.send_keys(char)
            assert sess.wait_for("find: first", timeout=5.0), sess.capture()
            sess.send_keys("Enter")
            assert sess.wait_until(
                lambda: "sort:data:desc,healthy" in sess.capture()
                and "[Recent]" not in sess.capture(),
                timeout=5.0,
            ), sess.capture()
            sess.send_keys("/")
            assert sess.wait_for("Filter ↑:", timeout=5.0), sess.capture()
            sess.send_keys("Enter")
            assert sess.wait_until(lambda: "Filter ↑:" not in sess.capture(), timeout=5.0), (
                sess.capture()
            )
        finally:
            sess.kill()

        sess = _launch(ws)
        try:
            sess.send_keys("f")
            assert sess.wait_for("[Recent]", timeout=5.0), sess.capture()
            sess.send_keys("Right", "Right")
            assert sess.wait_for("[Most Used]", timeout=5.0), sess.capture()
            screen = sess.capture()
            named = next(line for line in screen.splitlines() if "oldest-data-first" in line)
            unnamed = next(line for line in screen.splitlines() if "healthy=n" in line)
            assert unnamed.index("healthy=n") == named.index("oldest-data-first")
            assert unnamed.index("1x") == named.index("2x")
            sess.resize(36, 20)
            assert sess.wait_until(
                lambda: any(
                    "oldest-data-first" in line and "2x" in line and len(line) <= 36
                    for line in sess.capture().splitlines()
                ),
                timeout=5.0,
            ), sess.capture()
        finally:
            sess.kill()

    def test_reloads_filters_on_open(self, tmp_path: Path) -> None:
        """Filters added to configs/filters/ while the monitor is running show up
        on the next popup open — no restart required."""
        ws = _prepare_workspace(
            tmp_path,
            filter_yaml={"initial": "query: os=L\n"},
        )
        sess = _launch(ws)
        try:
            sess.send_keys("f")
            assert sess.wait_for("initial", timeout=5.0)
            sess.send_keys("Escape")
            assert sess.wait_until(lambda: "initial" not in sess.capture(), timeout=5.0)

            # Add a new filter on disk while the monitor is running.
            (tmp_path / "configs" / "filters" / "late-added.yaml").write_text("query: os=M\n")

            sess.send_keys("f")
            assert sess.wait_for("late-added", timeout=5.0), sess.capture()
            assert "initial" in sess.capture()
        finally:
            sess.kill()

    def test_empty_search_flashes_no_matches(self, tmp_path: Path) -> None:
        ws = _prepare_workspace(
            tmp_path,
            filter_yaml={"prod-talos": "query: os=L\n"},
        )
        sess = _launch(ws)
        try:
            sess.send_keys("f")
            assert sess.wait_for("prod-talos", timeout=5.0)
            for ch in "zzzz":
                sess.send_keys(ch)
            sess.send_keys("Enter")
            assert sess.wait_for("no matches", timeout=5.0), sess.capture()
            # Popup stays open
            assert "Saved" in sess.capture()
        finally:
            sess.kill()
