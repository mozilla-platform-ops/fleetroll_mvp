"""FleetRoll exception classes."""

from __future__ import annotations


class FleetRollError(RuntimeError):
    """Base exception for FleetRoll errors."""


class UserError(FleetRollError):
    """Errors that should be shown to user without traceback."""

    def __init__(self, message: str, rc: int = 2):
        super().__init__(message)
        self.rc = rc
