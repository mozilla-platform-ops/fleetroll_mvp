"""Reusable helpers for tmux-based TUI integration tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator


# ---------------------------------------------------------------------------
# Skip marker — applied automatically by tmux_session fixture
# ---------------------------------------------------------------------------

_TMUX_AVAILABLE = bool(shutil.which("tmux"))
_UV_PATH = shutil.which("uv") or "uv"

# Project root — needed so `uv run` finds pyproject.toml
_PROJECT_ROOT = Path(__file__).parent.parent

skip_no_tmux = pytest.mark.skipif(not _TMUX_AVAILABLE, reason="tmux not installed")


# ---------------------------------------------------------------------------
# TmuxSession
# ---------------------------------------------------------------------------


class TmuxSession:
    """Context-manager wrapper around a detached tmux session.

    Usage::

        with TmuxSession(cmd="fleetroll host-monitor hosts.txt", cols=120, rows=40) as sess:
            sess.wait_for("HOST", timeout=5.0)
            sess.send_keys("q")
    """

    def __init__(
        self,
        cmd: str,
        *,
        cols: int = 120,
        rows: int = 40,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> None:
        self.cmd = cmd
        self.cols = cols
        self.rows = rows
        self.env = env or {}
        self.cwd = cwd or _PROJECT_ROOT
        self.name = f"fleetroll-test-{uuid.uuid4().hex[:8]}"
        self._started = False

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.kill()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the tmux session in detached mode."""
        tmux_cmd = [
            "tmux",
            "new-session",
            "-d",
            "-s",
            self.name,
            "-x",
            str(self.cols),
            "-y",
            str(self.rows),
            "-c",
            str(self.cwd),
        ]
        # Pass extra env vars explicitly via -e so they bypass tmux's
        # update-environment filter (HOME is not in the default list).
        for key, value in self.env.items():
            tmux_cmd += ["-e", f"{key}={value}"]
        tmux_cmd.append(self.cmd)

        subprocess.run(
            tmux_cmd,
            check=True,
            capture_output=True,
        )
        self._started = True

    def kill(self) -> None:
        """Kill the tmux session, ignoring errors if already gone."""
        if not self._started:
            return
        subprocess.run(
            ["tmux", "kill-session", "-t", self.name],
            check=False,
            capture_output=True,
        )
        self._started = False

    def is_alive(self) -> bool:
        """Return True if the tmux session still exists."""
        result = subprocess.run(
            ["tmux", "has-session", "-t", self.name],
            check=False,
            capture_output=True,
        )
        return result.returncode == 0

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    def capture(self, *, with_escapes: bool = False) -> str:
        """Return the current pane contents as plain text.

        Returns empty string if the session no longer exists.

        Args:
            with_escapes: If True, include terminal escape sequences (-e flag).

        Returns:
            Screen text as a single string, or "" on failure.
        """
        cmd = ["tmux", "capture-pane", "-t", self.name, "-p"]
        if with_escapes:
            cmd.append("-e")
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return result.stdout if result.returncode == 0 else ""

    def send_keys(self, *keys: str) -> None:
        """Send one or more key sequences to the pane.

        Each positional argument is passed as a separate ``tmux send-keys`` call
        so that special key names (e.g. ``"Enter"``) work correctly.

        Args:
            *keys: Key names or characters to send.
        """
        for key in keys:
            subprocess.run(
                ["tmux", "send-keys", "-t", self.name, key, ""],
                check=True,
                capture_output=True,
            )

    def resize(self, cols: int, rows: int) -> None:
        """Resize the tmux window.

        Args:
            cols: New column count.
            rows: New row count.
        """
        subprocess.run(
            [
                "tmux",
                "resize-window",
                "-t",
                self.name,
                "-x",
                str(cols),
                "-y",
                str(rows),
            ],
            check=True,
            capture_output=True,
        )
        self.cols = cols
        self.rows = rows

    # ------------------------------------------------------------------
    # Polling helpers
    # ------------------------------------------------------------------

    def wait_for(self, text: str, *, timeout: float = 5.0) -> bool:
        """Poll capture-pane until *text* appears on screen.

        Args:
            text: Substring to look for in the rendered pane.
            timeout: Maximum seconds to wait.

        Returns:
            True if found, False if timeout expired.
        """
        return self.wait_until(lambda: text in self.capture(), timeout=timeout)

    def wait_until(
        self,
        predicate: Any,
        *,
        timeout: float = 5.0,
        interval: float = 0.1,
    ) -> bool:
        """Poll until *predicate()* returns truthy or timeout expires.

        Args:
            predicate: Zero-argument callable; returns truthy when condition met.
            timeout: Maximum seconds to wait.
            interval: Sleep interval between polls (seconds).

        Returns:
            True if predicate became truthy, False on timeout.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return True
            except Exception:  # noqa: S110
                pass
            time.sleep(interval)
        return False


# ---------------------------------------------------------------------------
# Database seeding helper
# ---------------------------------------------------------------------------


def seed_test_db(db_path: Path, hosts: list[str]) -> None:
    """Seed a fresh SQLite database with one observation record per host.

    Uses the real ``insert_host_observation()`` path to exercise the full
    data pipeline.

    Args:
        db_path: Path where the database should be created.
        hosts: List of fully-qualified hostnames to seed.
    """
    from fleetroll.db import get_connection, init_db, insert_host_observation

    init_db(db_path)
    conn = get_connection(db_path)
    try:
        ts_base = "2026-01-21T12:00:00+00:00"
        for i, host in enumerate(hosts):
            # Give each host a slightly different timestamp so ordering is stable
            ts = ts_base[:-6] + f":{i:02d}+00:00" if i < 60 else ts_base
            record = {
                "host": host,
                "ts": ts,
                "ok": 1,
                "observed": {
                    "role_present": True,
                    "role": "gecko_t_linux_talos",
                    "os_type": "Linux",
                    "override_present": True,
                    "override_sha256": "0000000000000001",
                    "vault_sha256": "00000000000000000000000000000001",
                    "override_meta": {"mtime_epoch": "1768983854"},
                    "uptime_s": 3600,
                },
            }
            insert_host_observation(conn, record)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmux_monitor_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Set up an isolated $HOME with seeded DB and host list file.

    Returns a dict with keys:
    - ``home``: Path to temporary home directory
    - ``db_path``: Path to seeded SQLite database
    - ``hosts_file``: Path to host list file
    - ``hosts``: List of host FQDNs
    """
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))

    hosts = [f"t-linux64-ms-{i:03d}.test.releng.mdc1.mozilla.com" for i in range(1, 6)]

    hosts_file = tmp_path / "hosts.txt"
    hosts_file.write_text("\n".join(hosts) + "\n")

    # Database lives under the fake $HOME
    fleetroll_dir = home_dir / ".fleetroll"
    fleetroll_dir.mkdir(parents=True, exist_ok=True)
    db_path = fleetroll_dir / "fleetroll.db"
    seed_test_db(db_path, hosts)

    return {
        "home": home_dir,
        "db_path": db_path,
        "hosts_file": hosts_file,
        "hosts": hosts,
    }


@pytest.fixture
def tmux_session(
    tmux_monitor_env: dict[str, Any],
) -> Generator[TmuxSession, None, None]:
    """Launch the monitor TUI in a tmux session and wait for initial render.

    Yields a ready-to-use :class:`TmuxSession`.  The session is killed on
    teardown regardless of test outcome.
    """
    if not _TMUX_AVAILABLE:
        pytest.skip("tmux not installed")

    hosts_file = tmux_monitor_env["hosts_file"]
    home_dir = tmux_monitor_env["home"]

    # Build env: start from current environment so PATH etc. are inherited,
    # then override HOME so fleetroll finds the test DB.
    session_env = {
        "HOME": str(home_dir),
        "PATH": os.environ.get("PATH", ""),
    }

    cmd = f"{_UV_PATH} run fleetroll host-monitor {hosts_file}"
    sess = TmuxSession(cmd=cmd, cols=160, rows=40, env=session_env)
    sess.start()

    try:
        # Wait for the TUI header to appear
        if not sess.wait_for("HOST", timeout=15.0):
            alive = sess.is_alive()
            output = sess.capture()
            sess.kill()
            pytest.fail(
                f"TUI did not render within timeout (session alive={alive}). Screen:\n{output}"
            )
        yield sess
    finally:
        sess.kill()
