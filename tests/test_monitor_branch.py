"""Raw branch queries and keyboard sorts share structured override evidence."""

import curses
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient
from fleetroll.cli import cli
from fleetroll.commands.monitor.cache import ShaInfoCache
from fleetroll.commands.monitor.curses_colors import CursesColors
from fleetroll.commands.monitor.data import build_row_values, override_branch_sort_key
from fleetroll.commands.monitor.display import MonitorDisplay
from fleetroll.commands.monitor.query import apply_query, parse_query, validate_query
from fleetroll.commands.web.app import create_app
from fleetroll.db import get_connection, get_db_path, init_db, insert_host_observation


@pytest.fixture
def branch_context(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    overrides = tmp_path / ".fleetroll" / "overrides"
    overrides.mkdir(parents=True)
    cache = ShaInfoCache(overrides, tmp_path / "vaults")
    records = {}
    # Branch order deliberately differs from checksum and input order.
    for host, branch, checksum in [
        ("host10", "Release", "test_zulu_checksum"),
        ("host2", "release", "test_yankee_checksum"),
        ("host3", "release-next", "test_xray_checksum"),
        ("host1", "Zulu", "test_alpha_checksum"),
        ("host5", None, "test_unknown_checksum"),
        ("host4", None, ""),
    ]:
        if branch:
            (overrides / checksum).write_text(
                f'PUPPET_BRANCH="{branch}"\nWORKER_TYPE_OVERRIDE="test-pool"\n'
            )
        records[host] = {
            "host": host,
            "ok": True,
            "ts": datetime.now(UTC).isoformat(),
            "observed": {
                "override_present": bool(checksum),
                "override_sha256": checksum,
                "os_type": "Linux",
            },
        }
    rows = [build_row_values(h, r, sha_cache=cache) for h, r in records.items()]
    return cache, records, rows


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("ovr_bch=release", {"host2", "host10"}),
        ("ovr_bch~release", {"host2", "host3", "host10"}),
        ("ovr_bch!=release", {"host1", "host3", "host4", "host5"}),
        ("ovr_bch~test-pool", set()),
        ("ovr_bch~*wt", set()),
        ("ovr_bch~test_zul", set()),
        ("ovr_bch=", {"host4", "host5"}),
        ("override=present ovr_bch=", {"host5"}),
        ("override=absent ovr_bch=", {"host4"}),
        ("ovr_bch!=", {"host1", "host2", "host3", "host10"}),
        ("ovr_bch=release|release-next", {"host2", "host3", "host10"}),
        ("ovr_checksum=test_zulu_checksum", {"host10"}),
        ("ovr_checksum=test_zul", set()),
        ("ovr_checksum~test_zul", {"host10"}),
        ("ovr_sha_full=test_zulu_checksum", {"host10"}),
        ("sha~test_zul", {"host10"}),
        ("ovr_sha~*wt", {"host1", "host2", "host3", "host10"}),
    ],
)
def test_branch_query_semantics(branch_context, query, expected):
    _, _, rows = branch_context
    parsed = parse_query(query)
    assert validate_query(parsed, query) is None
    assert {r["host"] for r in apply_query(rows, parsed)} == expected


def test_display_label_is_not_branch_evidence(branch_context, monkeypatch):
    cache, records, _ = branch_context
    monkeypatch.setattr(cache, "get_override_info", lambda sha: "a-human-label")
    row = build_row_values("host10", records["host10"], sha_cache=cache)
    assert row["sha"].startswith("a-human-label (")
    assert row["ovr_branch"] == "Release"
    row = build_row_values("host5", records["host5"], sha_cache=cache)
    assert row["ovr_branch"] == ""


def test_unknown_and_failed_observations_keep_presence_distinct(branch_context):
    cache, records, _ = branch_context
    for record in (None, {"ok": False}, {"ok": True, "observed": {}}):
        row = build_row_values("unknown", record, sha_cache=cache)
        assert row["ovr_branch"] == ""
        assert apply_query([row], parse_query("ovr_bch=")) == [row]
        assert not apply_query([row], parse_query("override=present"))
        assert not apply_query([row], parse_query("override=absent"))
    # A failed audit preserves the last known branch with the failed status.
    row = build_row_values("host10", {"ok": False}, last_ok=records["host10"], sha_cache=cache)
    assert row["status"] == "FAIL"
    assert row["override"] == "present"
    assert row["ovr_branch"] == "Release"


def test_branch_sort_respects_explicit_secondary_keys(branch_context):
    _, _, rows = branch_context
    selected = apply_query(rows, parse_query("ovr_bch=release sort:ovr_bch,host:desc"))
    # Explicit string host order, rather than the default natural tie-breaker.
    assert [r["host"] for r in selected] == ["host2", "host10"]
    selected = apply_query(rows, parse_query("ovr_bch=release sort:ovr_bch,host:asc"))
    assert [r["host"] for r in selected] == ["host10", "host2"]


def test_branch_sort_groups_hosts_by_full_override_checksum(branch_context, monkeypatch):
    cache, original, _ = branch_context
    records = {
        host: {**original[source], "host": host}
        for host, source in [
            ("host2", "host2"),
            ("host3", "host10"),
            ("host10", "host10"),
            ("host20", "host2"),
        ]
    }
    rows = [build_row_values(host, record, sha_cache=cache) for host, record in records.items()]
    display = make_display((cache, records, rows), monkeypatch=monkeypatch)
    display.sort_field = "ovr_bch"
    expected = ["host2", "host20", "host3", "host10"]
    assert sorted(display.hosts, key=display._get_sort_key) == expected  # noqa: SLF001
    for direction in ("asc", "desc"):
        sorted_rows = apply_query(rows, parse_query(f"sort:ovr_bch:{direction}"))
        assert [row["host"] for row in sorted_rows] == expected


@pytest.mark.parametrize("separator", ["-", "_"])
def test_dated_branches_stay_together(branch_context, monkeypatch, separator):
    cache, records, _ = branch_context
    branches = {
        "host10": f"20261104{separator}linux_upgrade_v115",
        "host2": f"20261105{separator}linux_upgrade_v115",
        "host3": "20261105-linux_networkmanager",
        "host1": "20261103-gwhc_zombies",
    }
    for host, branch in branches.items():
        checksum = records[host]["observed"]["override_sha256"]
        (cache.overrides_dir / checksum).write_text(f'PUPPET_BRANCH="{branch}"\n')
    # Use a fresh cache after changing the stored contents.
    cache = ShaInfoCache(cache.overrides_dir, cache.vault_dir)
    rows = [build_row_values(h, r, sha_cache=cache) for h, r in records.items()]
    display = make_display((cache, records, rows), monkeypatch=monkeypatch)
    display.sort_field = "ovr_bch"
    expected = ["host1", "host3", "host10", "host2", "host4", "host5"]
    assert sorted(display.hosts, key=display._get_sort_key) == expected  # noqa: SLF001
    assert [r["host"] for r in apply_query(rows, parse_query("sort:ovr_bch"))] == expected
    descending = ["host2", "host10", "host3", "host1", "host4", "host5"]
    assert [r["host"] for r in apply_query(rows, parse_query("sort:ovr_bch:desc"))] == descending
    query = f"ovr_bch={branches['host10']}"
    assert [r["host"] for r in apply_query(rows, parse_query(query))] == ["host10"]


@pytest.mark.parametrize(
    "branch", ["release", "123-release", "20261104", "20261104-", "x20261104-release"]
)
def test_other_branch_names_keep_full_sort_key(branch):
    assert override_branch_sort_key(branch) == (branch, branch)


def make_display(context, *, monkeypatch):
    cache, records, _ = context
    monkeypatch.setattr(CursesColors, "_init_curses", lambda self: None)
    monkeypatch.setattr(MonitorDisplay, "_check_log_sizes", lambda self: [])
    return MonitorDisplay(
        Mock(),
        hosts=list(records),
        host_source="test",
        latest=records,
        latest_ok=dict(records),
        tc_data={},
        sha_cache=cache,
        provider=Mock(load_windows_pools=dict),
        github_refs={},
    )


@pytest.mark.parametrize("failed", [False, True])
def test_keyboard_branch_sort_matches_query_and_retains_indicator(
    branch_context, monkeypatch, failed
):
    display = make_display(branch_context, monkeypatch=monkeypatch)
    if failed:
        display.latest = {h: {"ok": False} for h in display.hosts}
    display.sort_field = "role"
    display.curses_mod = curses
    display.draw_screen = Mock()
    display.handle_key(ord("s"))
    assert display.sort_field == "ovr_bch"
    expected = ["host2", "host10", "host3", "host1", "host4", "host5"]
    assert sorted(display.hosts, key=display._get_sort_key) == expected  # noqa: SLF001
    _, labels, _ = display._compute_column_widths(display.hosts)  # noqa: SLF001
    assert labels["sha"] == "OVR_BCH ↑"
    cache, _, _ = branch_context
    rows = [
        build_row_values(h, display.latest[h], last_ok=display.latest_ok[h], sha_cache=cache)
        for h in reversed(display.hosts)
    ]
    for direction, order in [
        ("asc", expected),
        ("desc", ["host1", "host3", "host2", "host10", "host4", "host5"]),
    ]:
        display.set_query(f"sort:ovr_bch:{direction}")
        assert [r["host"] for r in apply_query(rows, display._query)] == order  # noqa: SLF001
        _, labels, _ = display._compute_column_widths(display.hosts)  # noqa: SLF001
        assert labels["sha"] == "OVR_BCH " + ("↑" if direction == "asc" else "↓")


@pytest.mark.parametrize("surface", ["cli", "web"])
def test_branch_selection_and_sort_across_surfaces(branch_context, tmp_path, surface):
    _, records, _ = branch_context
    init_db(get_db_path())
    conn = get_connection(get_db_path())
    try:
        for record in records.values():
            insert_host_observation(conn, record)
        conn.commit()
    finally:
        conn.close()
    hosts_file = tmp_path / "hosts.list"
    hosts_file.write_text("\n".join(records) + "\n")
    cases = {
        "ovr_bch=release sort:ovr_bch": ["host2", "host10"],
        "ovr_bch= sort:ovr_bch": ["host4", "host5"],
        "ovr_checksum~test_zul": ["host10"],
        "sort:ovr_bch:desc": ["host1", "host3", "host2", "host10", "host4", "host5"],
    }
    if surface == "cli":
        runner = CliRunner()
        for query, expected in cases.items():
            result = runner.invoke(
                cli,
                [
                    "host",
                    "monitor",
                    str(hosts_file),
                    "--once",
                    "--hostname-only",
                    "--filter",
                    query,
                ],
            )
            assert result.exit_code == 0, result.output
            assert result.output.splitlines() == expected
        result = runner.invoke(
            cli,
            [
                "host",
                "monitor",
                str(hosts_file),
                "--once",
                "--hostname-only",
                "--sort",
                "ovr_bch",
            ],
        )
        assert result.exit_code == 0, result.output
        assert result.output.splitlines() == ["host2", "host10", "host3", "host1", "host4", "host5"]
        result = runner.invoke(
            cli,
            [
                "host",
                "monitor",
                str(hosts_file),
                "--once",
                "--hostname-only",
                "--filter-file",
                "configs/filters/linux-overrides.yaml",
            ],
        )
        assert result.exit_code == 0, result.output
        assert result.output.splitlines() == ["host10", "host2", "host3", "host1", "host5"]
    else:
        with TestClient(create_app()) as client:
            for query, expected in cases.items():
                result = client.get("/api/hosts", params={"filter": query})
                assert result.status_code == 200, result.text
                assert [r["host"] for r in result.json()["rows"]] == expected
