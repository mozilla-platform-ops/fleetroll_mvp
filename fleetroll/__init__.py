"""
FleetRoll - Fleet management tool for auditing and managing override files.

Design goals:
- No server required (CLI-only, matches spec intent).
- Uses your existing SSH client/config (so ProxyJump, bastions, agents work).
- Best-effort, explicit, and auditable.
"""

from __future__ import annotations

from .cli import main
from .constants import DEFAULT_OVERRIDE_PATH, DEFAULT_ROLE_PATH
from .exceptions import FleetRollError, UserError

__all__ = [
    "DEFAULT_OVERRIDE_PATH",
    "DEFAULT_ROLE_PATH",
    "FleetRollError",
    "UserError",
    "main",
]
