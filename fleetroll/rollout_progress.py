"""Interactive progress for the blocking phases of rollout comparisons."""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time


class RolloutProgress:
    """Keep a spinner alive while network requests or local evidence reads block."""

    def __init__(self, *, enabled=True, stream=None):
        self.stream = stream if stream is not None else sys.stderr
        self.enabled = enabled and self.stream.isatty() and os.environ.get("TERM") != "dumb"
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._label = "Preparing comparison"
        self._completed = None
        self._total = None
        self._detail = None
        self._started = time.monotonic()
        self._frame = 0

    def __enter__(self):
        if self.enabled:
            self._render()
            self._thread = threading.Thread(target=self._animate, daemon=True)
            self._thread.start()
        return self

    def update(self, label, *, completed=None, total=None, detail=None):
        if not self.enabled:
            return
        with self._lock:
            if label != self._label:
                self._write(final=True)
                self._label = label
                self._started = time.monotonic()
            self._completed, self._total = completed, total
            self._detail = detail
            self._write()

    def _write(self, *, final=False):
        count = f" {self._completed}/{self._total}" if self._total is not None else ""
        if self._detail:
            count += f"; {self._detail}"
        elapsed = time.monotonic() - self._started
        marker = " " if final else "|/-\\"[self._frame % 4]
        text = f"{marker} {self._label}{count} ({elapsed:.1f}s)"
        width = max(1, shutil.get_terminal_size(fallback=(100, 24)).columns - 1)
        self.stream.write("\r" + text[:width] + "\x1b[K" + ("\r\n" if final else ""))
        self.stream.flush()
        self._frame += 1

    def _render(self):
        with self._lock:
            self._write()

    def _animate(self):
        while not self._stop.wait(0.1):
            self._render()

    def __exit__(self, exc_type, exc, traceback):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        if self.enabled:
            with self._lock:
                if exc_type is not None:
                    self._label += " (stopped)"
                self._write(final=True)
