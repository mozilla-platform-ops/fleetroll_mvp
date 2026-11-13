"""Puppet results require changed, valid metadata bounded to the invocation."""

import pytest
from fleetroll.puppet_outcome import parse_run_observation, strip_run_markers

from tests.puppet_helpers import run_evidence


@pytest.mark.parametrize(
    ("code", "outcome", "changes"),
    [
        (0, "unchanged", False),
        (2, "changed", True),
        (1, "failed", None),
        (4, "failed", False),
        (6, "failed", True),
    ],
)
def test_detailed_puppet_result_is_independent_of_wrapper(code, outcome, changes):
    result = parse_run_observation(run_evidence(code))
    assert result["wrapper_exit"] == 0
    assert result["puppet_exit"] == code
    assert result["puppet_outcome"] == outcome
    assert result["changes_applied"] is changes
    assert result["outcome_source"] == "run_window_metadata_v1"


@pytest.mark.parametrize("after", ["missing", "unavailable", "", "not_base64", "e30="])
def test_unavailable_or_invalid_metadata_is_unknown(after):
    result = parse_run_observation(run_evidence(after=after))
    assert result["puppet_outcome"] == "unknown"
    assert result["puppet_exit"] is None
    assert result["changes_applied"] is None


def test_unchanged_metadata_is_not_this_runs_result():
    output = run_evidence(2)
    encoded = output.split("FLEETROLL_PUPPET_AFTER=", 1)[1].strip()
    result = parse_run_observation(run_evidence(2, before=encoded))
    assert result["puppet_outcome"] == "unknown"
    assert result["outcome_reason"] == "metadata_unchanged"


@pytest.mark.parametrize(
    "updates",
    [
        {"ts": "2026-11-13T11:59:59Z"},
        {"ts": "2026-11-13T12:00:12Z"},
        {"duration_s": 60},  # A previously running apply completed during our invocation.
    ],
)
def test_old_future_or_overlapping_run_is_not_attributed(updates):
    result = parse_run_observation(run_evidence(state_updates=updates))
    assert result["puppet_outcome"] == "unknown"
    assert result["outcome_reason"] == "metadata_outside_run_window"


@pytest.mark.parametrize(
    "updates",
    [
        {"schema_version": 2},
        {"schema_version": True},
        {"exit_code": True},
        {"exit_code": "2"},
        {"exit_code": -1},
        {"success": False},
        {"success": "true"},
        {"duration_s": -1},
        {"duration_s": True},
        {"duration_s": float("inf")},
        {"duration_s": float("nan")},
        {"ts": None},
        {"ts": "invalid"},
        {"ts": "2026-11-13T12:00:10"},
    ],
)
def test_invalid_or_unsupported_metadata_cannot_prove_success(updates):
    assert parse_run_observation(run_evidence(state_updates=updates))["puppet_outcome"] == "unknown"


@pytest.mark.parametrize(
    "output",
    [
        "EXIT=0\n",
        "EXIT=2\n",
        "",
        run_evidence() + "FLEETROLL_PUPPET_END=1794571211\n",
        run_evidence().replace("START=1794571200", "START=invalid"),
        run_evidence().replace("END=1794571211", "END=0"),
        run_evidence().replace("BEFORE=missing", "BEFORE=unavailable"),
    ],
)
def test_incomplete_duplicate_or_invalid_protocol_is_unknown(output):
    assert parse_run_observation(output)["puppet_outcome"] == "unknown"


def test_protocol_handles_pty_line_endings_and_is_hidden_from_console():
    output = run_evidence(2, output="Notice: applied catalog").replace("\n", "\r\n")
    assert parse_run_observation(output)["puppet_outcome"] == "changed"
    assert strip_run_markers(output) == "Notice: applied catalog"
