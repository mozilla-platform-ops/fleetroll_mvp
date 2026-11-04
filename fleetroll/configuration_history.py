"""Compact, bounded SQLite evidence independent of the rolling monitor snapshots."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .constants import OVERRIDES_DIR_NAME
from .rollout_history import (
    WRITE_ACTIONS,
    applied_time,
    parse_timestamp,
    repository_name,
    verified_configuration,
)

VERSION = 1
RETENTION_DAYS = 30
COMPACTION_GAP_SECONDS = 3600
# Deliberately exclude contents, output, paths, uptime, and polling/run durations.
STATE_FIELDS = (
    "role",
    "puppet_role",
    "override_present",
    "override_sha256",
    "puppet_override_sha_applied",
    "vault_present",
    "vault_sha256",
    "puppet_vault_sha_applied",
    "puppet_success",
    "puppet_git_dirty",
    "puppet_git_sha",
    "puppet_git_repo",
    "puppet_git_branch",
    "tc_worker_identity_status",
    "tc_worker_identity",
)
IDENTITY_FIELDS = ("provisionerId", "workerType", "workerGroup", "workerId")
SPAN_FIELDS = (
    "host",
    "first_ts",
    "last_ts",
    "state_id",
    "first_applied",
    "last_applied",
    "max_gap",
    "invalidated",
)


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


def database_path(conn: sqlite3.Connection) -> Path:
    return Path(conn.execute("PRAGMA database_list").fetchone()[2])


def history_available(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='configuration_history_meta'"
        ).fetchone()
        is not None
    )


def initialize(conn: sqlite3.Connection, *, audit_log: Path | None = None) -> None:
    """Idempotent additive migration; caller owns the transaction and commit."""
    statements = (
        "CREATE TABLE IF NOT EXISTS configuration_history_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        """CREATE TABLE IF NOT EXISTS configuration_states (
            id TEXT PRIMARY KEY, base_id TEXT NOT NULL, verified INTEGER NOT NULL,
            data TEXT NOT NULL, proof TEXT, reason TEXT)""",
        "CREATE INDEX IF NOT EXISTS configuration_states_base ON configuration_states(base_id, verified)",
        """CREATE TABLE IF NOT EXISTS configuration_spans (
            host TEXT NOT NULL, first_ts REAL NOT NULL, last_ts REAL NOT NULL,
            state_id TEXT NOT NULL, first_applied REAL, last_applied REAL,
            max_gap REAL NOT NULL DEFAULT 0, invalidated INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(host, first_ts, state_id))""",
        "CREATE INDEX IF NOT EXISTS configuration_spans_end ON configuration_spans(last_ts)",
        """CREATE TABLE IF NOT EXISTS configuration_boundaries (
            host TEXT NOT NULL, ts REAL NOT NULL, kind TEXT NOT NULL,
            source_ts REAL NOT NULL, PRIMARY KEY(host, source_ts, kind))""",
        "CREATE INDEX IF NOT EXISTS configuration_boundaries_time ON configuration_boundaries(ts)",
    )
    for statement in statements:
        conn.execute(statement)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(configuration_boundaries)")}
    if "source_ts" not in columns:
        conn.execute("ALTER TABLE configuration_boundaries ADD COLUMN source_ts REAL")
        conn.execute("UPDATE configuration_boundaries SET source_ts=ts")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS configuration_boundaries_source "
            "ON configuration_boundaries(host, source_ts, kind)"
        )
    inserted = conn.execute(
        "INSERT OR IGNORE INTO configuration_history_meta VALUES ('schema_version', ?)",
        (str(VERSION),),
    ).rowcount
    version = conn.execute(
        "SELECT value FROM configuration_history_meta WHERE key='schema_version'"
    ).fetchone()[0]
    if version != str(VERSION):
        raise sqlite3.DatabaseError(f"Unsupported configuration history schema version {version}")
    # The metadata INSERT acquired the writer lock before any bootstrap reads.
    from .audit import iter_audit_records

    path = database_path(conn)
    audit_log = audit_log or path.parent / "audit.jsonl"
    audit_observations = []
    for record in iter_audit_records(audit_log):
        if isinstance(record, dict) and record.get("action") in WRITE_ACTIONS:
            record_boundary(conn, record)
        elif inserted and isinstance(record, dict) and record.get("action") == "host.audit":
            stamp = parse_timestamp(record.get("ts"))
            if stamp and utc_now() - dt.timedelta(days=RETENTION_DAYS) <= stamp <= utc_now():
                audit_observations.append(record)
    if not inserted:
        cleanup(conn)
        return
    observations = [
        json.loads(data) for (data,) in conn.execute("SELECT data FROM host_observations")
    ]
    observations.extend(audit_observations)
    observations.sort(
        key=lambda record: parse_timestamp(record.get("ts"))
        or dt.datetime.min.replace(tzinfo=dt.UTC)
    )
    for record in observations:
        record_observation(conn, record, overrides_dir=audit_log.parent / OVERRIDES_DIR_NAME)
    cleanup(conn)


def state_payload(record: dict[str, Any]) -> dict[str, Any]:
    observed = record.get("observed") or {}
    state = {key: observed[key] for key in STATE_FIELDS if key in observed}
    if "puppet_git_repo" in state:
        repo = repository_name(state["puppet_git_repo"])
        state["puppet_git_repo"] = f"https://github.com/{repo}.git" if repo else None
    if isinstance(state.get("tc_worker_identity"), dict):
        state["tc_worker_identity"] = {
            key: state["tc_worker_identity"].get(key) for key in IDENTITY_FIELDS
        }
    return {"host": record["host"], "ok": bool(record.get("ok")), "observed": state}


def source_proof(conn, record, *, overrides_dir):
    """Pin verified applied configuration and the source of master/override proof."""
    observed = record.get("observed") or {}
    repo = repository_name(observed.get("puppet_git_repo"))
    ref = None
    if repo:
        owner, name = repo.split("/", 1)
        ref = conn.execute(
            """SELECT ts FROM github_refs WHERE owner=? AND repo=? AND branch='master'
               AND sha=? AND julianday(ts)<=julianday(?) ORDER BY ts LIMIT 1""",
            (owner, name, observed.get("puppet_git_sha"), record["ts"]),
        ).fetchone()
    revision = observed.get("puppet_git_sha")
    refs: set[tuple[str, str]] = (
        {(repo, revision)} if ref and repo and isinstance(revision, str) else set()
    )
    config, reason = verified_configuration(record, overrides_dir=overrides_dir, master_refs=refs)
    if config is None:
        return None, reason
    provenance = {
        "source": "host.audit",
        "observation_ts": record["ts"],
        "established_ts": record["ts"],
        "master_reference_ts": ref[0] if ref and not config.override_sha else None,
        "override_content_sha256": config.override_sha,
    }
    return {"version": VERSION, "configuration": asdict(config), "provenance": provenance}, None


def record_observation(conn, record, *, overrides_dir=None, now=None):
    """Persist endpoints before raw observation pruning, in the caller's transaction."""
    now = now or utc_now()
    stamp = parse_timestamp(record.get("ts"))
    if stamp is None or stamp > now or stamp < now - dt.timedelta(days=RETENTION_DAYS):
        return
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    payload = state_payload(record)
    applied = applied_time(record.get("observed") or {})
    # Missing/future application times must never inherit a valid descriptor.
    base_id = digest({**payload, "timestamp_valid": applied is not None and applied <= stamp})
    prior = conn.execute(
        "SELECT proof FROM configuration_states WHERE base_id=? AND verified=1 LIMIT 1",
        (base_id,),
    ).fetchone()
    overrides_dir = overrides_dir or database_path(conn).parent / OVERRIDES_DIR_NAME
    proof = json.loads(prior[0]) if prior else None
    reason = None
    if proof is None:
        proof, reason = source_proof(conn, record, overrides_dir=overrides_dir)
    eligible = bool(proof and applied and applied <= stamp)
    last_write = conn.execute(
        """SELECT max(ts) FROM configuration_boundaries
           WHERE host IN (?,?) AND ts<=? AND kind IN ('host.set_override','host.unset_override','host.set_vault')""",
        (record["host"], record["host"].split(".")[0], stamp.timestamp()),
    ).fetchone()[0]
    if (
        eligible
        and applied is not None
        and last_write is not None
        and applied.timestamp() < last_write
    ):
        eligible, reason = False, "configuration write awaits verified convergence"
    state_id = digest((base_id, eligible))
    conn.execute(
        "INSERT OR IGNORE INTO configuration_states VALUES (?, ?, ?, ?, ?, ?)",
        (
            state_id,
            base_id,
            int(eligible),
            encode(payload),
            encode(proof) if eligible else None,
            reason,
        ),
    )
    host, timestamp = record["host"], stamp.timestamp()
    applied_epoch = applied.timestamp() if applied else None
    cursor = conn.execute(
        """SELECT host, first_ts, last_ts, state_id, first_applied, last_applied, max_gap, invalidated
           FROM configuration_spans WHERE host=? ORDER BY last_ts DESC LIMIT 1""",
        (host,),
    )
    row = cursor.fetchone()
    latest = dict(zip(SPAN_FIELDS, row, strict=True)) if row else None
    if latest and timestamp <= latest["last_ts"]:
        overlapping = conn.execute(
            """SELECT state_id, first_ts, last_ts, first_applied, last_applied FROM configuration_spans
               WHERE host=? AND first_ts<=? AND last_ts>=?""",
            (host, timestamp, timestamp),
        ).fetchall()
        for key, first, last, first_apply, last_apply in overlapping:
            same_endpoint = (timestamp == first and applied_epoch == first_apply) or (
                timestamp == last and applied_epoch == last_apply
            )
            if key == state_id and (same_endpoint or first < timestamp < last):
                return
        # Do not reconstruct missing interior anchors from a late contradiction.
        conn.execute(
            "UPDATE configuration_spans SET invalidated=1 WHERE host=? AND first_ts<=? AND last_ts>=?",
            (host, timestamp, timestamp),
        )
        record_boundary(
            conn, {"host": host, "ts": record["ts"], "action": "history.conflict"}, now=now
        )
    else:
        boundary = conn.execute(
            "SELECT 1 FROM configuration_boundaries WHERE host IN (?,?) AND ts>=? AND ts<=? LIMIT 1",
            (host, host.split(".")[0], latest["last_ts"] if latest else timestamp, timestamp),
        ).fetchone()
        if latest and not latest["invalidated"] and latest["state_id"] == state_id and not boundary:
            gap = timestamp - latest["last_ts"]
            if gap <= COMPACTION_GAP_SECONDS:
                conn.execute(
                    """UPDATE configuration_spans SET last_ts=?, last_applied=?, max_gap=max(max_gap,?)
                       WHERE host=? AND first_ts=? AND state_id=?""",
                    (timestamp, applied_epoch, gap, host, latest["first_ts"], state_id),
                )
                prune_host(conn, host, now=now)
                return
    conn.execute(
        "INSERT OR IGNORE INTO configuration_spans VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
        (host, timestamp, timestamp, state_id, applied_epoch, applied_epoch),
    )
    prune_host(conn, host, now=now)


def record_boundary(conn, record, *, now=None):
    now = now or utc_now()
    stamp = parse_timestamp(record.get("ts"))
    if not isinstance(record.get("host"), str) or stamp is None:
        return
    if record.get("action") not in {*WRITE_ACTIONS, "history.conflict"}:
        return
    if stamp < now - dt.timedelta(days=RETENTION_DAYS):
        return
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    # Future event clocks cannot extend coverage or drive expiry.
    timestamp = min(stamp, now).timestamp()
    host = record["host"]
    if "." not in host:
        matches = conn.execute(
            "SELECT DISTINCT host FROM host_observations WHERE host LIKE ?",
            (host + ".%",),
        ).fetchall()
        # Ambiguity is uncertainty for every possible target, not a safe no-op.
        if matches:
            for (target,) in matches:
                record_boundary(conn, {**record, "host": target}, now=now)
            return
    inserted = conn.execute(
        "INSERT OR IGNORE INTO configuration_boundaries (host,ts,kind,source_ts) VALUES (?, ?, ?, ?)",
        (host, timestamp, record["action"], stamp.timestamp()),
    ).rowcount
    if inserted:
        conn.execute(
            "UPDATE configuration_spans SET invalidated=1 WHERE host=? AND first_ts<=? AND last_ts> ?",
            (host, timestamp, timestamp),
        )


def prune_host(conn, host, *, now):
    cutoff = (now - dt.timedelta(days=RETENTION_DAYS)).timestamp()
    conn.execute("DELETE FROM configuration_spans WHERE host=? AND last_ts<?", (host, cutoff))
    conn.execute("DELETE FROM configuration_boundaries WHERE host=? AND ts<?", (host, cutoff))


def cleanup(conn, *, now=None) -> dict[str, int]:
    if not history_available(conn):
        return {"spans": 0, "boundaries": 0, "descriptors": 0}
    now = now or utc_now()
    cutoff = (now - dt.timedelta(days=RETENTION_DAYS)).timestamp()
    result = {}
    result["spans"] = conn.execute(
        "DELETE FROM configuration_spans WHERE last_ts<?", (cutoff,)
    ).rowcount
    result["boundaries"] = conn.execute(
        "DELETE FROM configuration_boundaries WHERE ts<?", (cutoff,)
    ).rowcount
    result["descriptors"] = conn.execute(
        "DELETE FROM configuration_states WHERE id NOT IN (SELECT state_id FROM configuration_spans)"
    ).rowcount
    return result


def read_evidence(conn, *, until, now=None):
    """Return trusted endpoint records and metadata; never migrate/write here."""
    if not history_available(conn):
        return [], {"available": False, "retention_days": RETENTION_DAYS}
    version = conn.execute(
        "SELECT value FROM configuration_history_meta WHERE key='schema_version'"
    ).fetchone()[0]
    if version != str(VERSION):
        raise sqlite3.DatabaseError(f"Unsupported configuration history schema version {version}")
    now = now or utc_now()
    cutoff = now - dt.timedelta(days=RETENTION_DAYS)
    records = []
    spans = 0
    for row in conn.execute(
        """SELECT p.host,p.first_ts,p.last_ts,p.first_applied,p.last_applied,p.max_gap,p.invalidated,
                  s.data,s.proof,s.reason FROM configuration_spans p JOIN configuration_states s ON p.state_id=s.id
           WHERE p.last_ts>=? AND p.first_ts<=? ORDER BY p.host,p.first_ts""",
        (cutoff.timestamp(), until.timestamp()),
    ):
        host, first, last, first_apply, last_apply, gap, invalidated, data, proof_json, reason = row
        spans += 1
        payload, proof = json.loads(data), json.loads(proof_json) if proof_json else None
        for timestamp, applied in ((first, first_apply), (last, last_apply)):
            if timestamp > until.timestamp():
                continue
            record: dict[str, Any] = {
                **payload,
                "observed": dict(payload["observed"]),
                "host": host,
                "ts": dt.datetime.fromtimestamp(timestamp, dt.UTC).isoformat(),
                "action": "host.audit",
            }
            record["observed"]["puppet_state_ts"] = (
                dt.datetime.fromtimestamp(applied, dt.UTC).isoformat()
                if applied is not None
                else None
            )
            record["_configuration_proof"] = proof
            record["_compact_unverified"] = proof is None or bool(invalidated)
            record["_compact_invalidated"] = bool(invalidated)
            record["_compact_retention_cutoff"] = cutoff.isoformat()
            record["_compact_reason"] = (
                "late conflicting evidence invalidated span" if invalidated else reason
            )
            if timestamp == last and first != last and not invalidated:
                record["_compact_span_start"] = dt.datetime.fromtimestamp(first, dt.UTC).isoformat()
                record["_compact_max_gap"] = gap
            records.append(record)
    for host, timestamp, kind in conn.execute(
        "SELECT host,ts,kind FROM configuration_boundaries WHERE ts>=? AND ts<=?",
        (cutoff.timestamp(), until.timestamp()),
    ):
        targets = {host}
        if "." not in host:
            targets.update(r["host"] for r in records if r["host"].split(".")[0] == host)
        records.extend(
            {
                "host": target,
                "ts": dt.datetime.fromtimestamp(timestamp, dt.UTC).isoformat(),
                "action": kind,
            }
            for target in targets
        )
    return records, {
        "available": True,
        "schema_version": VERSION,
        "retention_days": RETENTION_DAYS,
        "cutoff": cutoff.isoformat(),
        "spans": spans,
        "source": "sqlite compact configuration history",
    }
