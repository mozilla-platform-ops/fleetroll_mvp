"""Compact vault display retains names, identity, and mnemonic colors."""

from unittest.mock import Mock

import pytest
from fleetroll.commands.monitor.cache import ShaInfoCache
from fleetroll.commands.monitor.formatting import (
    compute_columns_and_widths,
    format_monitor_row,
    format_vault_cell,
)
from fleetroll.commands.monitor.row_renderer import RowRenderer


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("-", "-"),
        ("?", "?"),
        ("test_sha", "test"),
        ("test_sha freddie-london", "test freddie-london"),
        ("test_sha freddie-london (linux_prod)", "test freddie-london ↪ linux_prod"),
    ],
)
def test_vault_format(raw, expected):
    assert format_vault_cell(raw) == expected


def test_vault_display_preserves_names_colors_and_original_values(tmp_path):
    checksum = "a" * 64
    cache = ShaInfoCache(overrides_dir=tmp_path, vault_dir=tmp_path)
    name = "linux_prod_with_a_long_name_that_should_remain_complete"
    cache.vault_cache[checksum] = name
    record = {"ok": True, "observed": {"vault_sha256": checksum}}
    draw = Mock()
    renderer = RowRenderer(safe_addstr=draw, colors=Mock())
    render_data = renderer.compute_row_render_data(
        "host1",
        latest={"host1": record},
        latest_ok={},
        tc_data={},
        fqdn_suffix=None,
        sha_cache=cache,
        github_refs={},
    )
    values = render_data["values"]
    assert values["vlt_sha"].startswith(checksum[:8])
    assert values["vlt_sha"].endswith(f"({name})")
    expected = f"aaaa {values['vlt_mnemonic']} ↪ {name}"
    _, widths = compute_columns_and_widths(
        hosts=["host1"],
        latest={"host1": record},
        sha_cache=cache,
        max_width=0,
    )
    assert widths["vlt_sha"] == len(expected)
    assert (
        format_monitor_row(
            "host1",
            record,
            sha_cache=cache,
            columns=["vlt_sha"],
            widths=widths,
        )
        == expected
    )
    renderer.draw_host_row(
        1,
        render_data=render_data,
        columns=["vlt_sha", "host"],
        widths={"vlt_sha": len(expected), "host": 5},
        color_maps={"sha": {}, "role": {}, "vlt_sha": {values["vlt_sha"]: 777}},
    )
    calls = [call.args for call in draw.call_args_list]
    assert (1, 0, "aaaa", 0) in calls
    assert (1, 5, values["vlt_mnemonic"], 777) in calls
    assert next(call for call in calls if call[2] == f"↪ {name}")[3] == 0
    assert (1, len(expected), " | ", 0) in calls


def test_hidden_vault_collision_expands_and_survives_filtering(tmp_path):
    first = "a" * 64
    second = "a" * 10 + "b" * 54
    cache = ShaInfoCache(overrides_dir=tmp_path, vault_dir=tmp_path)
    # Override collisions must not affect vault abbreviations.
    cache.register_override_checksums([second])
    assert cache.abbreviate_vault(first) == "aaaa"
    latest = {
        "host1": {"ok": True, "observed": {"vault_sha256": first}},
        "hidden-host": {"ok": True, "observed": {"vault_sha256": second}},
    }
    compute_columns_and_widths(hosts=["host1"], latest=latest, sha_cache=cache, max_width=0)
    assert cache.abbreviate_vault(first) == first[:11]
    assert cache.abbreviate_vault(second) == second[:11]
    compute_columns_and_widths(
        hosts=["host1"],
        latest={"host1": latest["host1"]},
        sha_cache=cache,
        max_width=0,
    )
    assert cache.abbreviate_vault(first) == first[:11]


def test_vault_inventory_counts_unnamed_files_and_filename_aliases(tmp_path):
    vault_dir = tmp_path / "vaults"
    vault_dir.mkdir()
    (vault_dir / "same_first_file").write_text("fake vault\n")
    (vault_dir / "same_second_file").write_text("fake vault\n")
    (vault_dir / "production").symlink_to("same_first_file")
    cache = ShaInfoCache(overrides_dir=tmp_path / "missing", vault_dir=vault_dir)
    cache.load_all()
    assert cache.abbreviate_vault("same_first_file") == "same_fir"
    assert cache.abbreviate_vault("same_second_file") == "same_sec"
    cache.register_vault_checksums(["blue_first_o", "blue_first_override_value"])
    assert cache.abbreviate_vault("blue_first_override_value") == "blue"
