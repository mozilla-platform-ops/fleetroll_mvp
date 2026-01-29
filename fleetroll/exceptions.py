"""FleetRoll exception classes."""

from __future__ import annotations


class FleetRollError(RuntimeError):
    """Base exception for FleetRoll errors."""


class UserError(FleetRollError):
    """Errors that should be shown to user without traceback."""

    def __init__(self, message: str, rc: int = 2):
        super().__init__(message)
        self.rc = rc


class CommandFailureError(FleetRollError):
    """Command failed - error message already printed, just need to exit.

    This exception is for cases where a command has already printed
    its error message and just needs to signal failure without
    additional output from main().
    """

    def __init__(self, rc: int = 1):
        super().__init__("")
        self.rc = rc
