"""Fixtures for the remote Puppet invocation evidence protocol."""

import base64
import json


def run_evidence(
    puppet_exit: int = 0,
    *,
    wrapper_exit: int = 0,
    before: str = "missing",
    after: str | None = None,
    state_updates: dict | None = None,
    output: str = "",
) -> str:
    state = {
        "schema_version": 1,
        "exit_code": puppet_exit,
        "success": puppet_exit in {0, 2},
        "ts": "2026-11-13T12:00:10Z",
        "duration_s": 10,
    }
    state.update(state_updates or {})
    encoded = after if after is not None else base64.b64encode(json.dumps(state).encode()).decode()
    return (
        f"{output}\n"
        f"FLEETROLL_PUPPET_WRAPPER_EXIT={wrapper_exit}\n"
        "FLEETROLL_PUPPET_START=1794571200\n"
        "FLEETROLL_PUPPET_END=1794571211\n"
        f"FLEETROLL_PUPPET_BEFORE={before}\n"
        f"FLEETROLL_PUPPET_AFTER={encoded}\n"
    )
