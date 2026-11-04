import hashlib

from click.testing import CliRunner
from fleetroll.cli import cli
from fleetroll.commands.fleet_status import build_report, render_report
from fleetroll.rollout_history import parse_timestamp


def test_population_override_and_uncertainty(tmp_path):
    content = b"PUPPET_BRANCH=merged-branch\n"
    sha = hashlib.sha256(content).hexdigest()
    (tmp_path / sha[:12]).write_bytes(content)
    record = {
        "host": "a.example",
        "ok": True,
        "ts": "2026-01-01T01:00:00Z",
        "observed": {
            "role": "talos",
            "override_present": True,
            "override_sha256": sha,
            "puppet_success": True,
            "puppet_override_sha_applied": sha,
            "puppet_state_ts": "2026-01-01T00:50:00Z",
        },
    }
    other = {**record, "host": "b", "observed": {**record["observed"], "role": "other"}}
    report = build_report(
        hosts=["a.example", "b", "missing"],
        latest={"a.example": {"ok": False, "ts": "2026-01-01T02:00:00Z"}},
        successful={"a.example": record, "b": other},
        writes={"a": parse_timestamp("2026-01-01T01:30:00Z")},
        role="talos",
        worker_type=None,
        overrides_dir=tmp_path,
        scoped=True,
    )
    assert report["matching_hosts"] == 1
    assert report["nonmatching_hosts"] == ["b"]
    assert report["unresolved_hosts"] == ["missing"]
    assert report["newer_failed_gathers"] == ["a.example"]
    assert report["unverified_writes"] == ["a.example"]
    group = report["groups"][0]
    assert (group["branch"], group["percentage"], group["hosts"]) == (
        "merged-branch",
        100,
        ["a.example"],
    )
    assert group["puppet_status"]["unknown"] == 1
    output = render_report(report)
    assert "Role: talos" in output
    assert "merged-branch" in output
    assert "Membership unresolved: 1 (1 without a successful observation)" in output
    assert "Outside selection: 1" in output
    assert "    b\n" not in output
    assert "Puppet: 1 unknown" in output
    assert "    b\n" in render_report(report, verbose=True)


def test_cli_selector_validation(monkeypatch):
    runner = CliRunner()
    assert runner.invoke(cli, ["fleet-status"]).exit_code == 2
    seen = []
    monkeypatch.setattr(
        "fleetroll.commands.fleet_status.run_fleet_status", lambda **kw: seen.append(kw)
    )
    for value, expected in [("pool", "releng-hardware/pool"), ("other/pool", "other/pool")]:
        assert runner.invoke(cli, ["fleet-status", "--worker-type", value]).exit_code == 0
        assert seen[-1]["worker_type"] == expected
    for value in ("/pool", "other/", "a/b/c"):
        assert runner.invoke(cli, ["fleet-status", "--worker-type", value]).exit_code == 2


def test_worker_identity_and_absent_override(tmp_path):
    record = {
        "host": "worker.example",
        "ok": True,
        "ts": "2026-01-01T02:00:00Z",
        "observed": {
            "tc_worker_identity_status": "ok",
            "tc_worker_identity": {
                "provisionerId": "prov",
                "workerType": "pool",
                "workerGroup": "group",
                "workerId": "worker",
            },
            "override_present": False,
            "puppet_success": True,
            "puppet_override_sha_applied": None,
            "puppet_state_ts": "2026-01-01T01:00:00Z",
        },
    }
    report = build_report(
        hosts=["worker.example"],
        latest={"worker.example": record},
        successful={"worker.example": record},
        writes={},
        role=None,
        worker_type="prov/pool",
        overrides_dir=tmp_path,
        scoped=False,
    )
    assert report["override_totals"]["absent"]["count"] == 1
    assert report["groups"][0]["puppet_status"]["applied"] == 1
