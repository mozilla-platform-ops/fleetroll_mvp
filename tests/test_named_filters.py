"""Tests for the named_filters loader."""

from __future__ import annotations

from pathlib import Path

import pytest
from fleetroll.commands.monitor.data import build_row_values
from fleetroll.commands.monitor.named_filters import (
    NamedFilter,
    load_named_filters,
)
from fleetroll.commands.monitor.query import apply_query, parse_query


@pytest.mark.parametrize(
    "record",
    [
        None,
        {"ok": False, "error": "unreachable"},
        {"ok": True, "observed": {"role_present": False}},
    ],
)
def test_moonshot_unobserved_filter_includes_missing_evidence(record):
    filters = load_named_filters(Path(__file__).resolve().parents[1] / "configs/filters")
    query = parse_query(next(f.query for f in filters if f.name == "linux-unobserved"))
    host = "t-linux64-ms-001.example.com"
    row = build_row_values(host, record)
    assert apply_query([row], query) == [row]
    known = {"host": host, "role": "gecko_t_linux_2404_talos"}
    assert apply_query([known], query) == []
    outside = {"host": "t-osx-001.example.com", "role": row["role"]}
    assert apply_query([outside], query) == []


def test_missing_dir_is_created(tmp_path: Path) -> None:
    configs_dir = tmp_path / "filters"
    assert not configs_dir.exists()
    result = load_named_filters(configs_dir)
    assert result == []
    assert configs_dir.is_dir()


def test_empty_dir_returns_empty_list(tmp_path: Path) -> None:
    configs_dir = tmp_path / "filters"
    configs_dir.mkdir()
    assert load_named_filters(configs_dir) == []


def test_loads_valid_filters_sorted(tmp_path: Path) -> None:
    configs_dir = tmp_path / "filters"
    configs_dir.mkdir()
    (configs_dir / "zeta.yaml").write_text("query: os=W\n")
    (configs_dir / "alpha.yaml").write_text("query: os=L role~talos\ndescription: Linux talos\n")
    result = load_named_filters(configs_dir)
    assert result == [
        NamedFilter(name="alpha", query="os=L role~talos", description="Linux talos"),
        NamedFilter(name="zeta", query="os=W", description=""),
    ]


def test_display_name_is_filename_stem(tmp_path: Path) -> None:
    configs_dir = tmp_path / "filters"
    configs_dir.mkdir()
    (configs_dir / "prod-talos.yaml").write_text("query: os=L\n")
    result = load_named_filters(configs_dir)
    assert result[0].name == "prod-talos"


def test_malformed_yaml_is_skipped(tmp_path: Path) -> None:
    configs_dir = tmp_path / "filters"
    configs_dir.mkdir()
    (configs_dir / "bad.yaml").write_text("key: [unclosed\n")
    (configs_dir / "ok.yaml").write_text("query: env=prod\n")
    result = load_named_filters(configs_dir)
    assert [f.name for f in result] == ["ok"]


def test_missing_query_is_skipped(tmp_path: Path) -> None:
    configs_dir = tmp_path / "filters"
    configs_dir.mkdir()
    (configs_dir / "nogquery.yaml").write_text("description: no query\n")
    (configs_dir / "empty-query.yaml").write_text("query: ''\n")
    (configs_dir / "ok.yaml").write_text("query: env=prod\n")
    result = load_named_filters(configs_dir)
    assert [f.name for f in result] == ["ok"]


def test_non_mapping_top_level_is_skipped(tmp_path: Path) -> None:
    configs_dir = tmp_path / "filters"
    configs_dir.mkdir()
    (configs_dir / "list.yaml").write_text("- query: os=L\n")
    (configs_dir / "ok.yaml").write_text("query: env=prod\n")
    result = load_named_filters(configs_dir)
    assert [f.name for f in result] == ["ok"]


def test_non_yaml_files_ignored(tmp_path: Path) -> None:
    configs_dir = tmp_path / "filters"
    configs_dir.mkdir()
    (configs_dir / "README.md").write_text("hi")
    (configs_dir / "filter.yml").write_text("query: os=L\n")  # only .yaml loaded
    (configs_dir / "ok.yaml").write_text("query: env=prod\n")
    result = load_named_filters(configs_dir)
    assert [f.name for f in result] == ["ok"]
