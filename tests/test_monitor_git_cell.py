"""Terminal Git prefixes share an inventory without changing evidence comparisons."""

from unittest.mock import Mock

import pytest
from fleetroll.commands.monitor.cache import ShaInfoCache, abbreviate_checksum
from fleetroll.commands.monitor.curses_colors import CursesColors
from fleetroll.commands.monitor.data import build_row_values
from fleetroll.commands.monitor.display import MonitorDisplay
from fleetroll.commands.monitor.formatting import (
    compute_columns_and_widths,
    format_monitor_row,
    register_checksum_observations,
    render_monitor_lines,
)
from fleetroll.commands.monitor.row_renderer import RowRenderer
from fleetroll.constants import DEFAULT_GITHUB_REPO


def observation(sha: str) -> dict:
    return {
        "ok": True,
        "observed": {"puppet_git_sha": sha, "puppet_success": True, "override_present": False},
    }


@pytest.mark.parametrize(
    ("first", "second", "length"),
    [
        ("blue_first_revision", "pink_second_revision", 4),
        ("same_first_revision", "same_second_revision", 8),
        ("shared_prefix_first", "shared_prefix_second", 15),
    ],
)
def test_git_pair_shares_prefix_rule_and_preserves_raw_values(first, second, length):
    record = observation(first)
    refs = {f"{DEFAULT_GITHUB_REPO}:master": {"sha": second}}
    raw = build_row_values("host1", record, github_refs=refs)
    compact = build_row_values("host1", record, github_refs=refs, git_checksums=set())
    assert raw["pp_sha"] == first[:7]
    assert raw["pp_exp"] == second[:7]
    assert compact["pp_sha"] == first[:length]
    assert compact["pp_exp"] == second[:length]
    assert compact["pp_match"] == raw["pp_match"] == "N"
    assert compact["healthy"] == raw["healthy"] == "N"


def test_matching_git_sha_uses_identical_four_character_prefix():
    sha = "blue_revision_value"
    refs = {f"{DEFAULT_GITHUB_REPO}:master": {"sha": sha}}
    values = build_row_values("host1", observation(sha), github_refs=refs, git_checksums=set())
    assert values["pp_sha"] == values["pp_exp"] == "blue"
    assert values["pp_match"] == "Y"


@pytest.mark.parametrize("use_cache", [True, False])
def test_hidden_hosts_refs_and_refreshes_keep_tui_prefixes(tmp_path, monkeypatch, use_cache):
    monkeypatch.setattr(CursesColors, "_init_curses", lambda self: None)
    monkeypatch.setattr(MonitorDisplay, "_check_log_sizes", lambda self: [])
    cache = ShaInfoCache(overrides_dir=tmp_path, vault_dir=tmp_path) if use_cache else None
    display = MonitorDisplay(
        None,
        hosts=["visible", "hidden"],
        host_source="hosts.list",
        latest={"visible": observation("same_first_revision")},
        latest_ok={"hidden": observation("same_second_revision")},
        sha_cache=cache,
        tc_data={},
        provider=Mock(load_windows_pools=dict),
        github_refs={f"{DEFAULT_GITHUB_REPO}:master": {"sha": "same_first_revision"}},
    )
    _, _, widths = display._compute_column_widths(["visible"])  # noqa: SLF001
    assert widths["pp_sha"] == widths["pp_exp"] == 8
    # Removing a hidden host from the next snapshot cannot shorten known prefixes.
    display.latest_ok.clear()
    display.github_refs["unused:branch"] = {"sha": "same_first_other"}
    _, _, widths = display._compute_column_widths(["visible"])  # noqa: SLF001
    assert widths["pp_sha"] == widths["pp_exp"] == 12
    display.github_refs.pop("unused:branch")
    _, _, widths = display._compute_column_widths(["visible"])  # noqa: SLF001
    assert widths["pp_sha"] == widths["pp_exp"] == 12
    draw = Mock()
    renderer = RowRenderer(safe_addstr=draw, colors=Mock())
    data = renderer.compute_row_render_data(
        "visible",
        latest=display.latest,
        latest_ok={},
        tc_data={},
        fqdn_suffix=None,
        sha_cache=cache,
        github_refs=display.github_refs,
        git_checksums=display._git_checksums,  # noqa: SLF001
    )
    renderer.draw_host_row(
        1,
        render_data=data,
        columns=["pp_sha", "pp_exp"],
        widths=widths,
        color_maps={"sha": {}, "vlt_sha": {}, "role": {}},
    )
    assert [call.args[2] for call in draw.call_args_list] == [
        "same_first_r",
        " | ",
        "same_first_r",
    ]


def test_text_widths_do_not_clip_long_collisions_or_failed_host_fallback(tmp_path):
    cache = ShaInfoCache(overrides_dir=tmp_path, vault_dir=tmp_path)
    first, second = "shared_prefix_first", "shared_prefix_second"
    latest = {"host1": {"ok": False}, "hidden": observation(second)}
    latest_ok = {"host1": observation(first)}
    refs = {f"{DEFAULT_GITHUB_REPO}:master": {"sha": second}}
    _, widths = compute_columns_and_widths(
        hosts=["host1"],
        latest=latest,
        latest_ok=latest_ok,
        github_refs=refs,
        sha_cache=cache,
        max_width=0,
    )
    assert widths["pp_sha"] == widths["pp_exp"] == 15
    assert (
        format_monitor_row(
            "host1",
            latest["host1"],
            last_ok=latest_ok["host1"],
            github_refs=refs,
            sha_cache=cache,
            columns=["pp_sha", "pp_exp"],
            widths=widths,
        )
        == "shared_prefix_f shared_prefix_s"
    )
    # The text path also handles hidden hosts when no metadata cache is supplied.
    header, lines = render_monitor_lines(
        hosts=["host1"],
        latest=latest,
        latest_ok=latest_ok,
        github_refs=refs,
        max_width=0,
    )
    assert "shared_prefix_f" in lines[0]
    assert "shared_prefix_s" in lines[0]
    assert "PP_SHA" in header


def test_git_inventory_is_independent_of_file_checksums(tmp_path):
    cache = ShaInfoCache(overrides_dir=tmp_path, vault_dir=tmp_path)
    cache.register_override_checksums(["blue_other_override"])
    cache.register_vault_checksums(["blue_other_vault"])
    known = register_checksum_observations(
        cache,
        latest={"host1": observation("blue_git_revision")},
        latest_ok={},
    )
    assert abbreviate_checksum("blue_git_revision", known=known) == "blue"


@pytest.mark.parametrize(
    ("record", "expected"), [(None, "?"), ({"ok": False}, "-"), (observation(""), "-")]
)
def test_missing_git_evidence_keeps_markers(record, expected):
    values = build_row_values("host1", record, git_checksums=set())
    assert values["pp_sha"] == values["pp_exp"] == expected
