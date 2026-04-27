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


def _run(args: DataFreshnessArgs, db_path: Path):
    with patch("fleetroll.db.get_db_path", return_value=db_path), pytest.raises(SystemExit) as exc:
        cmd_data_freshness(args)
    return exc.value.code


def _run_fresh(args: DataFreshnessArgs, db_path: Path):
    """Run command that is expected to exit 0 (fresh)."""
    with patch("fleetroll.db.get_db_path", return_value=db_path):
        cmd_data_freshness(args)


class TestNoDatabase:
    def test_exits_1_when_no_db(self, tmp_path, capsys):
        missing = tmp_path / "missing.db"
        args = DataFreshnessArgs(hosts_file=None, stale_threshold=None, json=False)
        code = _run(args, missing)
        assert code == 1

    def test_json_no_db(self, tmp_path, capsys):
        missing = tmp_path / "missing.db"
        args = DataFreshnessArgs(hosts_file=None, stale_threshold=None, json=True)
        _run(args, missing)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["status"] == "no_data"
        assert data["hosts_total"] == 0


class TestFresh:
    def test_fresh_exit_0(self, temp_db, capsys):
        conn = get_connection(temp_db)
        _insert_ok(conn, "host1.example.com", "2099-01-01T12:00:00+00:00")
        conn.commit()
        conn.close()

        args = DataFreshnessArgs(hosts_file=None, stale_threshold=None, json=False)
        _run_fresh(args, temp_db)

    def test_fresh_text_output(self, temp_db, capsys):
        conn = get_connection(temp_db)
        _insert_ok(conn, "host1.example.com", "2099-01-01T12:00:00+00:00")
        conn.commit()
        conn.close()

        args = DataFreshnessArgs(hosts_file=None, stale_threshold=None, json=False)
        _run_fresh(args, temp_db)
        out = capsys.readouterr().out
        assert "status:          fresh" in out
        assert "hosts_with_ok:   1/1" in out

    def test_fresh_json_output(self, temp_db, capsys):
        conn = get_connection(temp_db)
        _insert_ok(conn, "host1.example.com", "2099-01-01T12:00:00+00:00")
        conn.commit()
        conn.close()

        args = DataFreshnessArgs(hosts_file=None, stale_threshold=None, json=True)
        _run_fresh(args, temp_db)
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "fresh"
        assert data["hosts_total"] == 1
        assert data["hosts_with_ok"] == 1
        assert data["ok_age_seconds"] is not None
        assert data["stale_threshold_seconds"] == 3600


class TestStale:
    def test_stale_exit_1(self, temp_db, capsys):
        conn = get_connection(temp_db)
        _insert_ok(conn, "host1.example.com", "2020-01-01T00:00:00+00:00")
        conn.commit()
        conn.close()

        args = DataFreshnessArgs(hosts_file=None, stale_threshold=None, json=False)
        code = _run(args, temp_db)
        assert code == 1

    def test_stale_json_status(self, temp_db, capsys):
        conn = get_connection(temp_db)
        _insert_ok(conn, "host1.example.com", "2020-01-01T00:00:00+00:00")
        conn.commit()
        conn.close()

        args = DataFreshnessArgs(hosts_file=None, stale_threshold=None, json=True)
        _run(args, temp_db)
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "stale"

    def test_no_ok_records_is_no_data(self, temp_db, capsys):
        conn = get_connection(temp_db)
        _insert_fail(conn, "host1.example.com", "2099-01-01T12:00:00+00:00")
        conn.commit()
        conn.close()

        args = DataFreshnessArgs(hosts_file=None, stale_threshold=None, json=True)
        _run(args, temp_db)
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "no_data"
        assert data["hosts_with_ok"] == 0


class TestCustomThreshold:
    def test_custom_threshold_respected(self, temp_db, capsys):
        # 100s old record — stale at 3600s default, but fresh at 99999s threshold
        conn = get_connection(temp_db)
        _insert_ok(conn, "host1.example.com", "2020-01-01T00:00:00+00:00")
        conn.commit()
        conn.close()

        args = DataFreshnessArgs(hosts_file=None, stale_threshold=999999999, json=True)
        _run_fresh(args, temp_db)
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "fresh"
        assert data["stale_threshold_seconds"] == 999999999

    def test_custom_threshold_in_json(self, temp_db, capsys):
        conn = get_connection(temp_db)
        _insert_ok(conn, "host1.example.com", "2099-01-01T12:00:00+00:00")
        conn.commit()
        conn.close()

        args = DataFreshnessArgs(hosts_file=None, stale_threshold=7200, json=True)
        _run_fresh(args, temp_db)
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

        args = DataFreshnessArgs(hosts_file=str(hosts_file), stale_threshold=None, json=True)
        _run_fresh(args, temp_db)
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

        args = DataFreshnessArgs(hosts_file=str(hosts_file), stale_threshold=None, json=True)
        code = _run(args, temp_db)
        assert code == 1
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "stale"
