"""Spinner liveness, cleanup and output separation."""

import io
import threading

import pytest
from fleetroll.rollout_progress import RolloutProgress


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
    assert terminal.getvalue().endswith("\r\n")
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
