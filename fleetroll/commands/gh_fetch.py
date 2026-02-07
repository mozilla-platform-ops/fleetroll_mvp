"""GitHub branch refs fetch command."""

from __future__ import annotations

from ..github import do_github_fetch


def cmd_gh_fetch(*, override_delay: bool = False, quiet: bool = False) -> None:
    """Fetch GitHub branch refs for puppet repos.

    Args:
        override_delay: If True, force fetch regardless of throttle interval
        quiet: If True, use single-line output
    """
    do_github_fetch(override_delay=override_delay, quiet=quiet)
