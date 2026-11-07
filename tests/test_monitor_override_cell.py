"""Compact override identity preserves checksums, pool markers, and source values."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from fleetroll.commands.monitor.cache import ShaInfoCache
from fleetroll.commands.monitor.curses_colors import CursesColors
from fleetroll.commands.monitor.display import MonitorDisplay
from fleetroll.commands.monitor.formatting import (
    compute_columns_and_widths,
    format_monitor_row,
    format_override_cell,
    override_cell_parts,
    render_cell_text,
)
from fleetroll.commands.monitor.row_renderer import RowRenderer


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("-", "-"),
        ("?", "?"),
        ("test_sha", "test"),
        ("test_sha *wt", "test *wt"),
        ("main (test_sha)", "main test"),
        ("main (test_sha) *wt", "main test *wt"),
        (
            "feature-very-long-name-distinct (test_sha) *wt",
            "feature-very-long-name-distinct test *wt",
        ),
        ("abcdefghijklmnopqrstuvwxy (test_sha)", "abcdefghijklmnopqrstuvwxy test"),
        ("abcdefghijklmnopqrstuvwxyz (test_sha)", "abcdefghijklmnopqrstuvwxyz test"),
        ("branch*wt-name (test_sha)", "branch*wt-name test"),
    ],
)
def test_compact_override(raw, expected):
    assert format_override_cell(raw) == expected


def test_override_parts_preserve_complete_branch_and_identity_suffix():
    raw = "feature-very-long-name-distinct (test_sha) *wt"
    branch, checksum, marker = override_cell_parts(raw, checksum="test_sha")
    expected = "feature-very-long-name-distinct test_sha *wt"
    cell = render_cell_text("sha", raw, len(expected), override_checksum="test_sha")
    assert cell == expected
    assert cell.rstrip().endswith("test_sha *wt")
    assert checksum == "test_sha"
    assert marker == "*wt"
    assert branch == "feature-very-long-name-distinct"


@pytest.fixture
def override_context():
    checksum = "test_sha_override_value"
    cache = ShaInfoCache(overrides_dir=Path("/nonexistent"), vault_dir=Path("/nonexistent"))
    cache.override_cache[checksum] = {
        "branch": "feature-very-long-name-distinct",
        "user": None,
        "repo": None,
        "worker_type_override": "test-pool",
    }
    record = {
        "ok": True,
        "ts": "2026-11-07T00:00:00+00:00",
        "observed": {"override_present": True, "override_sha256": checksum},
    }
    return cache, {"host1": record}


@pytest.mark.parametrize("pool_override", [True, False])
def test_override_rendering_preserves_full_color_key_and_suffix(override_context, pool_override):
    cache, latest = override_context
    if not pool_override:
        cache.override_cache["test_sha_override_value"]["worker_type_override"] = None
    draw = Mock()
    renderer = RowRenderer(safe_addstr=draw, colors=Mock(red_attr=lambda: 600))
    render_data = renderer.compute_row_render_data(
        "host1",
        latest=latest,
        latest_ok=latest,
        tc_data={},
        fqdn_suffix=None,
        sha_cache=cache,
        github_refs={},
    )
    raw = render_data["values"]["sha"]
    assert raw == "feature-very-long-name-distinct (test_sha)" + (" *wt" if pool_override else "")
    expected = format_override_cell(raw)
    width = len(expected)
    renderer.draw_host_row(
        1,
        render_data=render_data,
        columns=["sha", "host"],
        widths={"sha": width, "host": 5},
        color_maps={"sha": {raw: 777}, "vlt_sha": {}, "role": {}},
    )
    calls = draw.call_args_list
    cell_calls = [call.args for call in calls if call.args[1] < width]
    assert "".join(call[2] for call in cell_calls).rstrip() == expected
    assert cell_calls[0][3] == 777
    assert next(call for call in cell_calls if call[2] == "test")[3] == 0
    if pool_override:
        assert next(call for call in cell_calls if call[2] == "*wt")[3] == 600
    assert any(call.args == (1, width, " | ", 0) for call in calls)


def test_override_width_in_text_and_tui(override_context, monkeypatch):
    cache, latest = override_context
    monkeypatch.setattr(CursesColors, "_init_curses", lambda self: None)
    monkeypatch.setattr(MonitorDisplay, "_check_log_sizes", lambda self: [])
    display = MonitorDisplay(
        None,
        hosts=["host1"],
        host_source="hosts.list",
        latest=latest,
        latest_ok=latest,
        sha_cache=cache,
        tc_data={},
        provider=Mock(load_windows_pools=dict),
        github_refs={},
    )
    _, _, tui_widths = display._compute_column_widths(["host1"])  # noqa: SLF001
    for cap_widths in (True, False):
        _, text_widths = compute_columns_and_widths(
            hosts=["host1"],
            latest=latest,
            latest_ok=latest,
            sha_cache=cache,
            max_width=0,
            cap_widths=cap_widths,
        )
        expected = "feature-very-long-name-distinct test *wt"
        assert text_widths["sha"] == tui_widths["sha"] == len(expected)
        cell = format_monitor_row(
            "host1",
            latest["host1"],
            last_ok=latest["host1"],
            sha_cache=cache,
            columns=["sha"],
            widths=text_widths,
        )
        assert cell == expected


def test_hidden_override_collision_uses_full_checksums(override_context):
    cache, latest = override_context
    latest["hidden-host"] = {
        "ok": True,
        "observed": {"override_sha256": "test_sha_second_value"},
    }
    _, widths = compute_columns_and_widths(
        hosts=["host1"],
        latest=latest,
        latest_ok=latest,
        sha_cache=cache,
        max_width=0,
    )
    cell = format_monitor_row(
        "host1",
        latest["host1"],
        sha_cache=cache,
        columns=["sha"],
        widths=widths,
    )
    assert cell.startswith("feature-very-long-name-distinct ")
    assert cell.endswith("test_sha_o *wt")
    assert cache.abbreviate_override("test_sha_second_value") == "test_sha_s"
    # Filtering away a known collision does not shorten the prefix again.
    compute_columns_and_widths(
        hosts=["host1"],
        latest={"host1": latest["host1"]},
        sha_cache=cache,
        max_width=0,
    )
    assert cache.abbreviate_override("test_sha_override_value") == "test_sha_o"


def test_checksum_inventory_includes_files_without_branch_metadata(tmp_path):
    for name in ("same_first_file", "same_second_file", "blue_unique_file"):
        (tmp_path / name).write_text("# no branch metadata\n")
    cache = ShaInfoCache(overrides_dir=tmp_path, vault_dir=tmp_path / "missing")
    cache.load_all()
    assert cache.abbreviate_override("same_first_file") == "same_fir"
    assert cache.abbreviate_override("same_second_file") == "same_sec"
    assert cache.abbreviate_override("blue_unique_file") == "blue"


def test_short_filename_alias_does_not_expand_its_full_checksum(tmp_path):
    cache = ShaInfoCache(overrides_dir=tmp_path, vault_dir=tmp_path / "missing")
    cache.register_override_checksums(["blue_first_o", "blue_first_override_value"])
    assert cache.abbreviate_override("blue_first_override_value") == "blue"


def test_extreme_collision_keeps_unique_checksum_and_pool_marker(override_context):
    cache, latest = override_context
    shared = "shared_prefix_" * 3
    first = shared + "first"
    second = shared + "second"
    latest["host1"]["observed"]["override_sha256"] = first
    cache.override_cache[first] = cache.override_cache.pop("test_sha_override_value")
    cache.register_override_checksums([second])
    _, widths = compute_columns_and_widths(
        hosts=["host1"],
        latest=latest,
        sha_cache=cache,
        max_width=0,
        cap_widths=True,
    )
    cell = format_monitor_row(
        "host1",
        latest["host1"],
        sha_cache=cache,
        columns=["sha"],
        widths=widths,
    )
    assert cell == "feature-very-long-name-distinct " + shared + "f *wt"
    assert widths["sha"] == len(cell)
