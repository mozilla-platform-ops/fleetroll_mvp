"""Queue identity can complete partial Puppet proof without guessing current state."""

import datetime as dt
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fleetroll import configuration_history as compact
from fleetroll import db
from fleetroll import rollout_report as report
from fleetroll.rollout_history import WorkerIdentity, build_history, classify_run
from fleetroll.rollout_report import (
    attribute_job,
    collect_host,
    load_evidence,
    summarize,
)

BASE = dt.datetime(2026, 11, 5, 4, tzinfo=dt.UTC)
UNTIL = BASE + dt.timedelta(hours=2)
HOST = "t-linux64-ms-017.example.test"
ROLE = "gecko_t_linux_2404_talos"
REPO = "mozilla-platform-ops/ronin_puppet"
IDENTITY = WorkerIdentity("releng-hardware", "test-pool", "mdc1", "t-linux64-ms-017")


def identity_json():
    return {
        "provisionerId": IDENTITY.provisioner,
        "workerType": IDENTITY.pool,
        "workerGroup": IDENTITY.worker_group,
        "workerId": IDENTITY.worker_id,
    }


def stamp(minute):
    return BASE + dt.timedelta(minutes=minute)


def observation(minute, *, sha=None, partial=True, **changes):
    observed = {
        "role": ROLE,
        "puppet_role": ROLE,
        "puppet_success": True,
        "puppet_git_dirty": False,
        "puppet_git_repo": f"https://github.com/{REPO}.git",
        "puppet_git_branch": "master",
        "puppet_git_sha": "test_old_revision",
        "puppet_state_ts": BASE.isoformat(),
        "override_present": sha is not None,
        "override_sha256": sha,
        "puppet_override_sha_applied": sha,
        "vault_present": False,
        "tc_worker_identity_status": "unreadable" if partial else "ok",
        "tc_worker_identity": None if partial else identity_json(),
    }
    observed.update(changes)
    return {
        "host": HOST,
        "action": "host.audit",
        "ok": True,
        "ts": stamp(minute).isoformat(),
        "observed": observed,
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(compact, "utc_now", lambda: UNTIL + dt.timedelta(days=1))
    path = tmp_path / "fleetroll.db"
    db.init_db(path)
    overrides = tmp_path / "overrides"
    overrides.mkdir()
    clients = SimpleNamespace(worker_manager=Mock(), queue=Mock())
    clients.worker_manager.getWorker.return_value = {
        "recentTasks": [{"taskId": "test_interrupted_task", "runId": 0}]
    }
    clients.queue.status.return_value = {
        "status": {
            "provisionerId": IDENTITY.provisioner,
            "workerType": IDENTITY.pool,
            "runs": [
                {
                    "runId": 0,
                    "workerGroup": IDENTITY.worker_group,
                    "workerId": IDENTITY.worker_id,
                    "state": "exception",
                    "started": "2026-11-05T04:44:06.847Z",
                    "resolved": "2026-11-05T05:04:11.596Z",
                }
            ],
        }
    }
    conn = db.get_connection(path)
    yield SimpleNamespace(
        path=path, conn=conn, overrides=overrides, clients=clients, audit=tmp_path / "audit.jsonl"
    )
    conn.close()


def records(*, sha=None):
    return [
        observation(0, sha=sha),
        observation(30, sha=sha),
        observation(60, sha=sha),
        observation(120, sha=sha, partial=False),
    ]


def collect(env, observations):
    return collect_host(
        HOST,
        records=observations,
        overrides_dir=env.overrides,
        master_refs=set(),
        since=BASE,
        until=UNTIL,
        max_gap=dt.timedelta(hours=1),
        worker_group="mdc1",
        clients=env.clients,
    )


def store(env, observations):
    for record in observations:
        db.insert_host_observation(env.conn, record, overrides_dir=env.overrides)
    env.conn.commit()


def test_partial_identity_history_attributes_only_the_queue_verified_run(env):
    result = collect(env, records())
    job = result["jobs"][0]
    assert job["configuration"]["git_sha"] == "test_old_revision"
    assert job["outcome"] == "exception"
    evidence = job["attribution_evidence"]
    assert evidence["method"] == "queue_run_identity_and_puppet_history"
    assert evidence["identity"]["identity"] == identity_json()
    assert evidence["host_association"]["source"] == "host_collected_configuration"
    assert evidence["host_association"]["observation_ts"] == UNTIL.isoformat()
    supported = next(i for i in result["intervals"] if i.get("identity_source") == "queue.status")
    assert supported["start"] == "2026-11-05T04:44:06.847000Z"
    assert supported["end"] == "2026-11-05T05:04:11.596000Z"
    assert result["configuration_gaps"] == [
        {
            "start": "2026-11-05T04:00:00Z",
            "end": "2026-11-05T04:44:06.847000Z",
        },
        {"start": "2026-11-05T05:04:11.596000Z", "end": "2026-11-05T06:00:00Z"},
    ]
    assessment = summarize([result])
    assert assessment["coverage"]["classified"] == 1
    assert assessment["cohorts"][0]["outcomes"]["exception"] == 1


def test_attribution_does_not_upgrade_unverified_current_configuration(env):
    observations = records()
    observations[-1]["observed"]["puppet_success"] = False
    # Keep the task entirely inside the old verified Puppet window.
    env.clients.queue.status.return_value["status"]["runs"][0]["resolved"] = "2026-11-05T04:50:00Z"
    result = collect(env, observations)
    assert result["configuration"] is None
    assert result["jobs"][0]["configuration"] is not None
    assert summarize([result])["unverified_hosts"] == [HOST]


def test_report_json_exposes_new_attribution_and_keeps_exception_exit_status(
    env, monkeypatch, capsys
):
    store(env, records())
    env.clients.queue.taskQueueCounts.return_value = {"pendingTasks": 0}
    env.clients.queue.task.return_value = {"metadata": {"name": "test_interrupted_workload"}}
    monkeypatch.setattr(report, "load_tc_credentials", Mock())
    monkeypatch.setattr(report, "create_tc_clients", Mock(return_value=env.clients))
    code = report.main(
        [
            HOST,
            "--db",
            str(env.path),
            "--audit-log",
            str(env.audit),
            "--since",
            BASE.isoformat(),
            "--until",
            UNTIL.isoformat(),
            "--no-task-name-cache",
            "--json",
        ]
    )
    result = json.loads(capsys.readouterr().out)
    assert code == 1
    assert result["schema_version"] == 1
    assert result["summary"]["coverage"]["classified"] == 1
    assert (
        result["hosts"][0]["jobs"][0]["attribution_evidence"]["method"]
        == "queue_run_identity_and_puppet_history"
    )
    assert result["summary"]["cohorts"][0]["outcomes"]["exception"] == 1


def test_revision_transition_keeps_before_and_after_separate(env):
    observations = records()
    for record in observations[2:]:
        record["observed"].update(
            puppet_git_sha="test_new_revision", puppet_state_ts=stamp(55).isoformat()
        )
    statuses = {}
    for task_id, start, end in [
        ("test_before", 20, 25),
        ("test_crossing", 44, 64),
        ("test_after", 65, 70),
    ]:
        statuses[task_id] = {
            "status": {
                "provisionerId": IDENTITY.provisioner,
                "workerType": IDENTITY.pool,
                "runs": [
                    {
                        "runId": 0,
                        "workerGroup": IDENTITY.worker_group,
                        "workerId": IDENTITY.worker_id,
                        "state": "exception" if task_id == "test_crossing" else "completed",
                        "started": stamp(start).isoformat(),
                        "resolved": stamp(end).isoformat(),
                    }
                ],
            }
        }
    env.clients.worker_manager.getWorker.return_value = {
        "recentTasks": [{"taskId": key, "runId": 0} for key in statuses]
    }
    env.clients.queue.status.side_effect = statuses.__getitem__
    result = collect(env, observations)
    jobs = {job["task_id"]: job for job in result["jobs"]}
    assert jobs["test_before"]["configuration"]["git_sha"] == "test_old_revision"
    assert jobs["test_after"]["configuration"]["git_sha"] == "test_new_revision"
    assert jobs["test_crossing"]["configuration"] is None
    summary = summarize([result])
    assert summary["coverage"]["classified"] == 2
    assert summary["unattributed_outcomes"]["outcomes"]["exception"] == 1


@pytest.mark.parametrize(
    "kind",
    [
        "identity",
        "ambiguous",
        "malformed",
        "puppet",
        "vault",
        "vault_unknown",
        "vault_absent",
        "write",
        "gap",
        "revision",
    ],
)
def test_contradictions_and_unverified_state_remain_boundaries(env, kind):
    observations = records()
    change = observations[1]["observed"]
    if kind == "identity":
        change.update(
            tc_worker_identity_status="ok",
            tc_worker_identity={**identity_json(), "workerId": "another-worker"},
        )
    elif kind == "ambiguous":
        change["tc_worker_identity_status"] = "ambiguous"
    elif kind == "malformed":
        change.update(
            tc_worker_identity_status="ok", tc_worker_identity={"workerId": IDENTITY.worker_id}
        )
    elif kind == "puppet":
        change["puppet_success"] = False
    elif kind == "vault":
        change.update(
            vault_present=True,
            vault_sha256="test_deployed_vault",
            puppet_vault_sha_applied="test_other_vault",
        )
    elif kind == "vault_unknown":
        change["vault_present"] = None
    elif kind == "vault_absent":
        change["puppet_vault_sha_applied"] = "test_old_vault"
    elif kind == "write":
        observations.insert(
            2,
            {"host": HOST, "action": "host.set_override", "ok": False, "ts": stamp(40).isoformat()},
        )
    elif kind == "gap":
        observations = [observations[0], observations[-1]]
    else:
        for record in observations[2:]:
            record["observed"].update(
                puppet_git_sha="test_new_revision", puppet_state_ts=stamp(55).isoformat()
            )
    result = collect(env, observations)
    assert result["jobs"][0]["configuration"] is None
    assert result["jobs"][0]["exclusion_reason"]


def test_failed_gather_remains_absence_of_evidence(env):
    observations = records()
    observations.insert(
        2,
        {
            "host": HOST,
            "ts": stamp(45).isoformat(),
            "ok": False,
            "action": "host.audit",
            "observed": {"puppet_success": False},
        },
    )
    assert collect(env, observations)["jobs"][0]["configuration"] is not None


@pytest.mark.parametrize("field", ["provisionerId", "workerType", "workerGroup", "workerId"])
def test_full_queue_run_identity_is_required(env, field):
    status = env.clients.queue.status.return_value["status"]
    target = status if field in ("provisionerId", "workerType") else status["runs"][0]
    target[field] = "different-identity"
    job = collect(env, records())["jobs"][0]
    assert job["query_error"]
    assert job["configuration"] is None
    assert job["identity_evidence"] is None


def test_current_identity_is_not_a_substitute_for_queue_run_proof(env):
    intervals = build_history(
        records(),
        overrides_dir=env.overrides,
        master_refs=set(),
        max_gap=dt.timedelta(hours=1),
        task_identity=IDENTITY,
    )
    result = attribute_job(
        {
            "task_id": "test_task",
            "run_id": 0,
            "outcome": "completed",
            "started": stamp(44).isoformat(),
            "resolved": stamp(50).isoformat(),
            "query_error": None,
        },
        intervals=[],
        task_intervals=intervals,
        identity=IDENTITY,
        since=BASE,
        until=UNTIL,
    )
    assert result["configuration"] is None


def test_legacy_host_mapping_cannot_complete_partial_identity_history(env):
    job = collect(env, records())["jobs"][0]
    intervals = build_history(
        records(),
        overrides_dir=env.overrides,
        master_refs=set(),
        max_gap=dt.timedelta(hours=1),
        task_identity=IDENTITY,
    )
    result = attribute_job(
        job,
        intervals=[],
        task_intervals=intervals,
        identity=replace(IDENTITY, legacy=True),
        since=BASE,
        until=UNTIL,
    )
    assert result["configuration"] is None


@pytest.mark.parametrize("kind", ["duplicate", "boolean"])
def test_ambiguous_or_malformed_queue_run_ids_are_unresolved(env, kind):
    status = env.clients.queue.status.return_value["status"]
    if kind == "duplicate":
        status["runs"].append(dict(status["runs"][0]))
    else:
        env.clients.worker_manager.getWorker.return_value["recentTasks"][0]["runId"] = 1
        status["runs"][0]["runId"] = True
    job = collect(env, records())["jobs"][0]
    assert job["query_error"]
    assert job["identity_evidence"] is None
    assert job["configuration"] is None


@pytest.mark.parametrize("source", ["snapshot", "audit"])
def test_external_observations_cannot_inject_trusted_descriptor_proofs(env, source):
    store(env, [observation(120, partial=False)])
    checksum = hashlib.sha256(b"untrusted_override").hexdigest()
    snapshots = []
    for minute in (0, 30, 60):
        record = observation(minute, sha=checksum)
        record["_configuration_proof"] = {
            "version": 2,
            "configuration": {
                "role": ROLE,
                "repository": REPO,
                "branch": "forged",
                "override_sha": checksum,
                "git_sha": "test_old_revision",
                "vault_sha": None,
            },
            "identity": None,
            "provenance": {"established_ts": BASE.isoformat()},
        }
        snapshots.append(record)
    files = []
    if source == "snapshot":
        path = env.path.parent / "untrusted.json"
        path.write_text(json.dumps(snapshots))
        files.append(path)
    else:
        env.audit.write_text("\n".join(json.dumps(r) for r in snapshots) + "\n")
    evidence, _, _ = load_evidence(
        env.path,
        audit_log=env.audit,
        until=UNTIL,
        observation_files=files,
    )
    assert collect(env, evidence[HOST])["jobs"][0]["configuration"] is None


def test_latest_identity_failure_still_prevents_live_queries(env):
    observations = records()
    observations[-1]["observed"].update(
        tc_worker_identity_status="unreadable", tc_worker_identity=None
    )
    result = collect(env, observations)
    assert result["query_ok"] is False
    assert result["error"]
    env.clients.worker_manager.getWorker.assert_not_called()


def test_independent_puppet_proof_round_trips_after_override_cache_loss(env):
    content = f'PUPPET_REPO="https://github.com/{REPO}.git"\nPUPPET_BRANCH="test-rollout"\n'
    checksum = hashlib.sha256(content.encode()).hexdigest()
    cached = env.overrides / checksum[:12]
    cached.write_text(content)
    observations = [observation(minute, sha=checksum) for minute in range(0, 61, 3)]
    observations.append(observation(120, sha=checksum, partial=False))
    store(env, observations)
    cached.unlink()
    retained, _ = compact.read_evidence(env.conn, until=UNTIL)
    assert any(r.get("_configuration_proof", {}).get("version") == 2 for r in retained)
    partial = next(
        r for r in retained if r["observed"]["tc_worker_identity_status"] == "unreadable"
    )
    assert partial["_configuration_proof"]["identity"] is None
    result = collect(env, retained)
    assert result["jobs"][0]["configuration"]["override_sha256"] == checksum
    assert result["jobs"][0]["attribution_evidence"]["interval"]["source"] in {
        "compact",
        "raw+compact",
    }


def test_legacy_full_proof_remains_readable(env):
    observations = [observation(0, partial=False), observation(120, partial=False)]
    store(env, observations)
    for state_id, encoded in env.conn.execute(
        "SELECT id,proof FROM configuration_states WHERE proof IS NOT NULL"
    ).fetchall():
        proof = json.loads(encoded)
        proof["version"] = 1
        proof["configuration"]["identity"] = proof.pop("identity")
        proof.pop("identity_reason", None)
        env.conn.execute(
            "UPDATE configuration_states SET proof=? WHERE id=?", (json.dumps(proof), state_id)
        )
    retained, _ = compact.read_evidence(env.conn, until=UNTIL)
    assert collect(env, retained)["configuration"] is not None


def test_old_identity_only_descriptors_can_be_strictly_revalidated(env):
    observations = records()
    store(env, observations)
    for state_id, data in env.conn.execute("SELECT id,data FROM configuration_states").fetchall():
        if json.loads(data)["observed"].get("tc_worker_identity_status") == "unreadable":
            env.conn.execute(
                "UPDATE configuration_states SET proof=NULL,verified=0,reason=? WHERE id=?",
                ("worker identity collection failed; refresh gather host", state_id),
            )
    # Simulate raw observation pruning while retaining the latest complete endpoint.
    env.conn.execute("DELETE FROM host_observations WHERE ts<?", (stamp(120).isoformat(),))
    env.conn.commit()
    evidence, _, _ = load_evidence(env.path, audit_log=env.audit, until=UNTIL)
    job = collect(env, evidence[HOST])["jobs"][0]
    assert job["configuration"] is not None
    assert job["attribution_evidence"]["interval"]["source"] == "revalidated_compact"
    saved = env.path.parent / "saved-observations.json"
    saved.write_text(json.dumps(observations))
    evidence, _, _ = load_evidence(
        env.path, audit_log=env.audit, until=UNTIL, observation_files=[saved]
    )
    assert collect(env, evidence[HOST])["jobs"][0]["configuration"] is not None


@pytest.mark.parametrize(
    "blocked",
    ["write_reason", "old_application", "invalidated", "missing_artifact", "unsupported_proof"],
)
def test_old_endpoint_revalidation_cannot_bypass_unknown_proof(env, blocked):
    content = f'PUPPET_REPO="https://github.com/{REPO}.git"\nPUPPET_BRANCH="test-rollout"\n'
    checksum = hashlib.sha256(content.encode()).hexdigest()
    cached = env.overrides / checksum[:12]
    cached.write_text(content)
    observations = records(sha=checksum)
    store(env, observations)
    retained, _ = compact.read_evidence(env.conn, until=UNTIL)
    for record in retained:
        if record["observed"].get("tc_worker_identity_status") != "unreadable":
            continue
        record["_configuration_proof"] = None
        record["_compact_unverified"] = True
        record["_compact_reason"] = "worker identity collection failed; refresh gather host"
        if blocked == "write_reason":
            record["_compact_reason"] = "configuration write awaits verified convergence"
        elif blocked == "old_application":
            record["_compact_retention_cutoff"] = stamp(1).isoformat()
        elif blocked == "invalidated":
            record["_compact_invalidated"] = True
        elif blocked == "unsupported_proof":
            record["_configuration_proof"] = {"version": 99}
    if blocked == "missing_artifact":
        cached.unlink()
    assert collect(env, retained)["jobs"][0]["configuration"] is None


def test_partial_compacted_span_cannot_invent_an_interior_horizon(env):
    store(env, [observation(minute) for minute in range(0, 91, 3)])
    cutoff = stamp(75)
    retained, metadata = compact.read_evidence(env.conn, until=cutoff)
    assert metadata["historical_cutoffs"][0]["puppet_verified"] is True
    assert metadata["historical_cutoffs"][0]["verified"] is False
    intervals = build_history(
        retained,
        overrides_dir=env.overrides,
        master_refs=set(),
        max_gap=dt.timedelta(hours=1),
        task_identity=IDENTITY,
    )
    assert classify_run(intervals, started=stamp(44), ended=stamp(64))[0] is None
