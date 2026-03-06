#!/usr/bin/env python3
"""start_gather_tmux.py — launch a multi-window tmux session for fleetroll gather."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PaneSpec:
    """Specification for a single tmux window."""

    name: str
    commands: list[str] = field(default_factory=list)


class TmuxLauncher:
    """Create and populate a tmux session with multiple windows.

    Args:
        session_name: Name for the tmux session.
        num_panes: Total number of windows to create.
        root: Working directory for the session.
        default_shell: Shell to use in each window (default: zsh).
        panes: Per-window specs (name + commands). Extras beyond num_panes
               are ignored; windows without a spec get a generic shell window.
    """

    def __init__(
        self,
        session_name: str,
        num_panes: int,
        root: str | Path,
        *,
        default_shell: str = "zsh",
        panes: list[PaneSpec] | None = None,
    ) -> None:
        self.session_name = session_name
        self.num_panes = num_panes
        self.root = Path(root).expanduser()
        self.default_shell = default_shell
        self.panes = panes or []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(list(args), check=True, text=True)

    def _send(self, window: int, text: str, *, enter: bool = True) -> None:
        target = f"{self.session_name}:{window}"
        keys = [text, "C-m"] if enter else [text]
        self._run("tmux", "send-keys", "-t", target, *keys)

    def _session_exists(self) -> bool:
        result = subprocess.run(
            ["tmux", "list-sessions"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
        return self.session_name in result.stdout

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def launch(self) -> None:
        """Create the session and populate all windows. Exits if session exists."""
        if self._session_exists():
            print(
                f"tmux session '{self.session_name}' already exists. Exiting.",
                file=sys.stderr,
            )
            sys.exit(1)

        print(f"* No tmux session '{self.session_name}' detected. Starting...")

        # Create detached session (this gives us window 0 for free).
        self._run(
            "tmux",
            "new-session",
            "-d",
            "-s",
            self.session_name,
            "-c",
            str(self.root),
        )

        # Create remaining windows.
        for _ in range(1, self.num_panes):
            self._run(
                "tmux",
                "new-window",
                "-t",
                self.session_name,
                "-c",
                str(self.root),
            )

        # Configure each window.
        for i in range(self.num_panes):
            spec = self.panes[i] if i < len(self.panes) else PaneSpec(name=self.default_shell)
            self._run(
                "tmux",
                "rename-window",
                "-t",
                f"{self.session_name}:{i}",
                spec.name,
            )
            for cmd in spec.commands:
                self._send(i, cmd)

        # Return focus to window 0 so the user lands there on attach.
        self._run("tmux", "select-window", "-t", f"{self.session_name}:0")

        print(
            f"\nTo attach to the tmux session, run:\n   tmux attach-session -t {self.session_name}"
        )


# ---------------------------------------------------------------------------
# Script-specific configuration (mirrors start_gather_tmux.sh)
# ---------------------------------------------------------------------------


def main() -> None:
    import os

    launcher = TmuxLauncher(
        session_name="fleetroll_gather",
        num_panes=5,
        root=os.path.expanduser("~/git/fleetroll_mvp"),
        default_shell="zsh",
        panes=[
            PaneSpec(
                name="gather-canary",
                commands=[
                    "watchp -n 3m ./tools/gather-generic.sh configs/host-lists/linux/canary-all.list"
                ],
            ),
            PaneSpec(
                name="gather-linux",
                commands=[
                    "watchp -n 10m ./tools/gather-generic.sh configs/host-lists/linux/all.list"
                ],
            ),
            PaneSpec(
                name="gather-mac",
                commands=[
                    "watchp -n 15m ./tools/gather-generic.sh configs/host-lists/mac/all.list"
                ],
            ),
            PaneSpec(
                name="gather-windows",
                commands=[
                    "watchp -n 15m ./tools/gather-generic.sh configs/host-lists/windows/all.list"
                ],
            ),
        ],
    )
    launcher.launch()


if __name__ == "__main__":
    main()
