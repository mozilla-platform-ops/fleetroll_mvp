"""Tests for data-freshness command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fleetroll.cli_types import DataFreshnessArgs
from fleetroll.commands.data_freshness import cmd_data_freshness
from fleetroll.db import get_connection, init_db, insert_host_observation


@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary database."""
    db_path = tmp_path / "fleetroll.db"
    init_db(db_path)
    return db_path


def _insert_ok(conn, host, ts):
    insert_host_observation(conn, {"host": host, "ts": ts, "ok": 1, "observed": {}})


def _insert_fail(conn, host, ts):
    insert_host_observation(conn, {"host": host, "ts": ts, "ok": 0, "observed": {}})


def _args(*, hosts_file=None, all_hosts=False, stale_threshold=None, min_fresh_pct=80, json=False):
    return DataFreshnessArgs(
        hosts_file=hosts_file,
        all_hosts=all_hosts,
        stale_threshold=stale_threshold,
        min_fresh_pct=min_fresh_pct,
        json=json,
    )


def _run(args: DataFreshnessArgs, db_path: Path):
    with patch("fleetroll.db.get_db_path", return_value=db_path), pytest.raises(SystemExit) as exc:
        cmd_data_freshness(args)
    return exc.value.code


def _run_fresh(args: DataFreshnessArgs, db_path: Path):
    """Run command that is expected to exit 0 (fresh)."""
    with patch("fleetroll.db.get_db_path", return_value=db_path):
        cmd_data_freshness(args)


class TestNoDatabase:
    def test_exits_1_when_no_db(self, tmp_path):
        missing = tmp_path / "missing.db"
        code = _run(_args(all_hosts=True), missing)
        assert code == 1

    def test_json_no_db(self, tmp_path, capsys):
        missing = tmp_path / "missing.db"
        _run(_args(all_hosts=True, json=True), missing)
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "no_data"
        assert data["hosts_total"] == 0


class TestAllHostsMode:
    def test_fresh_exit_0_all_hosts(self, temp_db):
        conn = get_connection(temp_db)
        _insert_ok(conn, "host1.example.com", "2099-01-01T12:00:00+00:00")
        conn.commit()
        conn.close()
        _run_fresh(_args(all_hosts=True), temp_db)

    def test_fresh_text_output(self, temp_db, capsys):
        conn = get_connection(temp_db)
        _insert_ok(conn, "host1.example.com", "2099-01-01T12:00:00+00:00")
        conn.commit()
        conn.close()
        _run_fresh(_args(all_hosts=True), temp_db)
        out = capsys.readouterr().out
        assert "status:          fresh" in out
        assert "hosts_with_ok:   1/1" in out

    def test_fresh_json_output(self, temp_db, capsys):
        conn = get_connection(temp_db)
        _insert_ok(conn, "host1.example.com", "2099-01-01T12:00:00+00:00")
        conn.commit()
        conn.close()
        _run_fresh(_args(all_hosts=True, json=True), temp_db)
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "fresh"
        assert data["hosts_total"] == 1
        assert data["hosts_with_ok"] == 1
        assert data["ok_age_seconds"] is not None
        assert data["stale_threshold_seconds"] == 3600

    def test_stale_exit_1(self, temp_db):
        conn = get_connection(temp_db)
        _insert_ok(conn, "host1.example.com", "2020-01-01T00:00:00+00:00")
        conn.commit()
        conn.close()
        code = _run(_args(all_hosts=True), temp_db)
        assert code == 1

    def test_stale_json_status(self, temp_db, capsys):
        conn = get_connection(temp_db)
        _insert_ok(conn, "host1.example.com", "2020-01-01T00:00:00+00:00")
        conn.commit()
        conn.close()
        _run(_args(all_hosts=True, json=True), temp_db)
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "stale"

    def test_no_ok_records_is_no_data(self, temp_db, capsys):
        conn = get_connection(temp_db)
        _insert_fail(conn, "host1.example.com", "2099-01-01T12:00:00+00:00")
        conn.commit()
        conn.close()
        _run(_args(all_hosts=True, json=True), temp_db)
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "no_data"
        assert data["hosts_with_ok"] == 0

    def test_empty_db_is_no_data(self, temp_db, capsys):
        _run(_args(all_hosts=True, json=True), temp_db)
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "no_data"
        assert data["hosts_total"] == 0


class TestPercentageCheck:
    def test_single_fresh_host_out_of_many_is_stale(self, temp_db, capsys):
        """The original bug: 1 fresh host out of 5 must not report fresh at 80%."""
        conn = get_connection(temp_db)
        _insert_ok(conn, "host1.example.com", "2099-01-01T12:00:00+00:00")  # fresh
        _insert_ok(conn, "host2.example.com", "2020-01-01T00:00:00+00:00")  # stale
        _insert_ok(conn, "host3.example.com", "2020-01-01T00:00:00+00:00")  # stale
        _insert_ok(conn, "host4.example.com", "2020-01-01T00:00:00+00:00")  # stale
        _insert_ok(conn, "host5.example.com", "2020-01-01T00:00:00+00:00")  # stale
        conn.commit()
        conn.close()
        code = _run(_args(all_hosts=True, json=True), temp_db)
        assert code == 1
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "stale"
        assert data["hosts_fresh"] == 1
        assert data["fresh_pct"] == 20.0

    def test_all_hosts_fresh(self, temp_db, capsys):
        conn = get_connection(temp_db)
        _insert_ok(conn, "host1.example.com", "2099-01-01T12:00:00+00:00")
        _insert_ok(conn, "host2.example.com", "2099-01-01T12:00:00+00:00")
        conn.commit()
        conn.close()
        _run_fresh(_args(all_hosts=True, json=True), temp_db)
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "fresh"
        assert data["hosts_fresh"] == 2
        assert data["fresh_pct"] == 100.0

    def test_exactly_at_min_pct_is_fresh(self, temp_db, capsys):
        """4 of 5 hosts fresh = 80% = exactly at threshold → fresh."""
        conn = get_connection(temp_db)
        _insert_ok(conn, "host1.example.com", "2099-01-01T12:00:00+00:00")
        _insert_ok(conn, "host2.example.com", "2099-01-01T12:00:00+00:00")
        _insert_ok(conn, "host3.example.com", "2099-01-01T12:00:00+00:00")
        _insert_ok(conn, "host4.example.com", "2099-01-01T12:00:00+00:00")
        _insert_ok(conn, "host5.example.com", "2020-01-01T00:00:00+00:00")
        conn.commit()
        conn.close()
        _run_fresh(_args(all_hosts=True, min_fresh_pct=80, json=True), temp_db)
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "fresh"
        assert data["fresh_pct"] == 80.0

    def test_one_below_min_pct_is_stale(self, temp_db, capsys):
        """3 of 5 hosts fresh = 60% < 80% → stale."""
        conn = get_connection(temp_db)
        _insert_ok(conn, "host1.example.com", "2099-01-01T12:00:00+00:00")
        _insert_ok(conn, "host2.example.com", "2099-01-01T12:00:00+00:00")
        _insert_ok(conn, "host3.example.com", "2099-01-01T12:00:00+00:00")
        _insert_ok(conn, "host4.example.com", "2020-01-01T00:00:00+00:00")
        _insert_ok(conn, "host5.example.com", "2020-01-01T00:00:00+00:00")
        conn.commit()
        conn.close()
        code = _run(_args(all_hosts=True, min_fresh_pct=80, json=True), temp_db)
        assert code == 1
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "stale"

    def test_min_fresh_pct_100_requires_all_fresh(self, temp_db, capsys):
        conn = get_connection(temp_db)
        _insert_ok(conn, "host1.example.com", "2099-01-01T12:00:00+00:00")
        _insert_ok(conn, "host2.example.com", "2020-01-01T00:00:00+00:00")
        conn.commit()
        conn.close()
        code = _run(_args(all_hosts=True, min_fresh_pct=100, json=True), temp_db)
        assert code == 1
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "stale"

    def test_min_fresh_pct_in_json(self, temp_db, capsys):
        conn = get_connection(temp_db)
        _insert_ok(conn, "host1.example.com", "2099-01-01T12:00:00+00:00")
        conn.commit()
        conn.close()
        _run_fresh(_args(all_hosts=True, min_fresh_pct=50, json=True), temp_db)
        data = json.loads(capsys.readouterr().out)
        assert data["min_fresh_pct"] == 50


class TestCustomThreshold:
    def test_custom_threshold_respected(self, temp_db, capsys):
        conn = get_connection(temp_db)
        _insert_ok(conn, "host1.example.com", "2020-01-01T00:00:00+00:00")
        conn.commit()
        conn.close()
        _run_fresh(_args(all_hosts=True, stale_threshold=999999999, json=True), temp_db)
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "fresh"
        assert data["stale_threshold_seconds"] == 999999999

    def test_custom_threshold_in_json(self, temp_db, capsys):
        conn = get_connection(temp_db)
        _insert_ok(conn, "host1.example.com", "2099-01-01T12:00:00+00:00")
        conn.commit()
        conn.close()
        _run_fresh(_args(all_hosts=True, stale_threshold=7200, json=True), temp_db)
        data = json.loads(capsys.readouterr().out)
        assert data["stale_threshold_seconds"] == 7200


class TestHostsFile:
    def test_hosts_file_filters_to_specified_hosts(self, temp_db, tmp_path, capsys):
        conn = get_connection(temp_db)
        _insert_ok(conn, "host1.example.com", "2099-01-01T12:00:00+00:00")
        _insert_ok(conn, "host2.example.com", "2020-01-01T00:00:00+00:00")
        conn.commit()
        conn.close()

        hosts_file = tmp_path / "hosts.txt"
        hosts_file.write_text("host1.example.com\n")

        _run_fresh(_args(hosts_file=str(hosts_file), json=True), temp_db)
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "fresh"
        assert data["hosts_total"] == 1
        assert data["hosts_with_ok"] == 1

    def test_hosts_file_stale_when_all_hosts_stale(self, temp_db, tmp_path, capsys):
        conn = get_connection(temp_db)
        _insert_ok(conn, "host1.example.com", "2099-01-01T12:00:00+00:00")
        _insert_ok(conn, "host2.example.com", "2020-01-01T00:00:00+00:00")
        conn.commit()
        conn.close()

        hosts_file = tmp_path / "hosts.txt"
        hosts_file.write_text("host2.example.com\n")

        code = _run(_args(hosts_file=str(hosts_file), json=True), temp_db)
        assert code == 1
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "stale"

    def test_hosts_file_single_stale_out_of_many_is_stale(self, temp_db, tmp_path, capsys):
        """The bug: 1 fresh host must not satisfy 80% when most are stale."""
        conn = get_connection(temp_db)
        for i in range(1, 6):
            ts = "2099-01-01T12:00:00+00:00" if i == 1 else "2020-01-01T00:00:00+00:00"
            _insert_ok(conn, f"host{i}.example.com", ts)
        conn.commit()
        conn.close()

        hosts_file = tmp_path / "hosts.txt"
        hosts_file.write_text("\n".join(f"host{i}.example.com" for i in range(1, 6)) + "\n")

        code = _run(_args(hosts_file=str(hosts_file), json=True), temp_db)
        assert code == 1
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "stale"
        assert data["hosts_fresh"] == 1
