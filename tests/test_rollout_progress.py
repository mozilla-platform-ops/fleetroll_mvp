"""Spinner liveness, cleanup and output separation."""

import io
import os
import threading

import pytest
from fleetroll.rollout_progress import RolloutProgress, progress_timing


class Terminal(io.StringIO):
    def __init__(self):
        super().__init__()
        self.animated = threading.Event()

    def isatty(self):
        return True

    def write(self, text):
        result = super().write(text)
        if text.startswith("\r/"):
            self.animated.set()
        return result


def test_spinner_animates_while_work_is_blocked_and_cleans_up(monkeypatch):
    monkeypatch.setenv("TERM", "xterm")
    terminal = Terminal()
    threads = set(threading.enumerate())
    with RolloutProgress(stream=terminal) as progress:
        # No updates or work completion are needed for the spinner to advance.
        assert terminal.animated.wait(timeout=2)
        progress.update("Fetching task names", completed=1, total=2)
        progress.update("Fetching task names", completed=2, total=2)
    assert "Fetching task names 2/2" in terminal.getvalue()
    assert terminal.getvalue().endswith("\r\n\r\n")
    assert set(threading.enumerate()) <= threads


def test_spinner_stops_on_exception(monkeypatch):
    monkeypatch.setenv("TERM", "xterm")
    terminal = Terminal()
    threads = set(threading.enumerate())

    def fail():
        with RolloutProgress(stream=terminal) as progress:
            progress.update("Loading evidence")
            raise ValueError("collection failed")

    with pytest.raises(ValueError, match="collection failed"):
        fail()
    assert "Loading evidence (stopped)" in terminal.getvalue()
    assert set(threading.enumerate()) <= threads


@pytest.mark.parametrize("mode", ["redirected", "disabled", "dumb"])
def test_noninteractive_or_disabled_progress_is_silent(monkeypatch, mode):
    monkeypatch.setenv("TERM", "dumb" if mode == "dumb" else "xterm")
    stream = io.StringIO() if mode == "redirected" else Terminal()
    with RolloutProgress(stream=stream, enabled=mode != "disabled") as progress:
        progress.update("Fetching task names", completed=1, total=2)
    assert stream.getvalue() == ""
    assert not progress.enabled


@pytest.mark.parametrize(
    ("completed", "total", "elapsed", "expected"),
    [
        (0, 10, 5, " (5.0s)"),
        (0, 0, 5, " (5.0s)"),
        (1, 10, 0, " (0.0s)"),
        (1, 10, 0.5, " (0.5s)"),
        (4, 10, 2, " (2.0s; 2.0/s; ETA ~3s)"),
        (10, 10, 2, " (2.0s; 5.0/s)"),
        (None, None, 2, " (2.0s)"),
    ],
)
def test_phase_estimates_need_elapsed_time_and_work(completed, total, elapsed, expected):
    assert progress_timing(completed=completed, total=total, elapsed=elapsed) == expected


def test_timing_stays_visible_in_narrow_terminal_and_resets_between_phases(monkeypatch):
    from fleetroll import rollout_progress

    now = 100
    monkeypatch.setattr(rollout_progress.time, "monotonic", lambda: now)
    monkeypatch.setattr(
        rollout_progress.shutil, "get_terminal_size", lambda **_: os.terminal_size((80, 24))
    )
    monkeypatch.setenv("TERM", "xterm")
    terminal = Terminal()
    progress = RolloutProgress(stream=terminal)
    progress.update("Fetching task names", completed=0, total=10, detail="2375 cached")
    now += 2
    progress.update("Fetching task names", completed=4, total=10, detail="2375 cached; " * 20)
    assert "2.0/s; ETA ~3s" in terminal.getvalue()
    progress.update("Matching workloads")
    final = terminal.getvalue().split("\r")[-1]
    assert "(0.0s)" in final
    assert "ETA" not in final
