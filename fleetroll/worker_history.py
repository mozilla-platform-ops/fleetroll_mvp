"""Durable, bounded historical worker identity evidence; never reset authorization."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .configuration_history import STATE_FIELDS
from .rollout_history import parse_timestamp

if TYPE_CHECKING:
    import sqlite3

    from .commands.monitor.cache import ShaInfoCache

IDENTITY_FIELDS = {
    "provisioner": "provisionerId",
    "worker_type": "workerType",
    "worker_group": "workerGroup",
    "worker_id": "workerId",
}
OVERRIDE_ACTIONS = {"host.set_override", "host.unset_override"}


def collected_identity(record: dict) -> dict | None:
    """Only successful, complete host collections qualify; never infer identity."""
    observed = record.get("observed") or {}
    raw = observed.get("tc_worker_identity")
    if not record.get("ok") or observed.get("tc_worker_identity_status") != "ok":
        return None
    if not isinstance(raw, dict):
        return None
    identity = {key: raw.get(field) for key, field in IDENTITY_FIELDS.items()}
    return identity if all(isinstance(v, str) and v.strip() for v in identity.values()) else None


def load_history(conn: sqlite3.Connection, *, hosts: list[str]) -> dict[str, dict]:
    if not hosts:
        return {}
    placeholders = ",".join("?" for _ in hosts)
    return {
        host: json.loads(data)
        for host, data in conn.execute(
            f"SELECT host,data FROM worker_identity_history WHERE host IN ({placeholders})",  # noqa: S608 - placeholders only
            hosts,
        )
    }


def _save(conn: sqlite3.Connection, *, host: str, state: dict) -> None:
    conn.execute(
        "INSERT INTO worker_identity_history VALUES (?,?) "
        "ON CONFLICT(host) DO UPDATE SET data=excluded.data",
        (host, json.dumps(state, sort_keys=True)),
    )


def _stamp(record: dict | None) -> datetime:
    return parse_timestamp((record or {}).get("ts")) or datetime.min.replace(tzinfo=UTC)


def _override(record: dict) -> tuple:
    observed = record.get("observed") or {}
    return observed.get("override_present"), observed.get("override_sha256")


def record_observation(conn: sqlite3.Connection, *, record: dict) -> None:
    """Update before raw pruning, in the observation's existing transaction."""
    host = record["host"]
    if parse_timestamp(record.get("ts")) is None:
        return
    state = load_history(conn, hosts=[host]).get(host, {})
    before = json.dumps(state, sort_keys=True)
    if "." in host:
        short_state = load_history(conn, hosts=[host.split(".")[0]]).get(host.split(".")[0], {})
        if _stamp(short_state.get("override_write")) > _stamp(state.get("override_write")):
            state["override_write"] = short_state["override_write"]
    compact: dict[str, Any] = {
        "host": host,
        "ts": record["ts"],
        "ok": bool(record.get("ok")),
        "observed": {
            key: value
            for key, value in (record.get("observed") or {}).items()
            if key in STATE_FIELDS
        },
    }
    # Whitelist the four identity fields even for malformed/untrusted imported records.
    raw = compact["observed"].get("tc_worker_identity")
    if isinstance(raw, dict):
        compact["observed"]["tc_worker_identity"] = {
            key: raw.get(key) for key in IDENTITY_FIELDS.values()
        }
    previous = state.get("latest_success")
    if (
        previous
        and compact["ok"]
        and _stamp(compact) == _stamp(previous)
        and compact["observed"] != previous["observed"]
    ):
        state["observation_conflict"] = {"ts": compact["ts"]}
    if compact["ok"] and _stamp(compact) >= _stamp(previous):
        if previous and _override(previous) != _override(compact):
            # A remotely observed change is also a boundary, even without a local write.
            # Retain the previous audit time: this audit may itself prove convergence.
            change = {
                "ts": previous["ts"],
                "evidence_ts": compact["ts"],
                "source": "observed_override_change",
                "override_present": compact["observed"].get("override_present"),
                "override_sha256": compact["observed"].get("override_sha256"),
            }
            if _stamp(change) > _stamp(state.get("override_change")):
                state["override_change"] = change
        state["latest_success"] = compact
        if compact["observed"].get("tc_worker_identity_status") == "missing":
            state["missing_configuration"] = {"ts": compact["ts"]}
    if collected_identity(compact) and _stamp(compact) >= _stamp(state.get("baseline")):
        state["baseline"] = compact
    if state and json.dumps(state, sort_keys=True) != before:
        _save(conn, host=host, state=state)


def record_write(conn: sqlite3.Connection, *, record: dict) -> None:
    """Retain the latest known override write even with no baseline or later audit."""
    if record.get("action") not in OVERRIDE_ACTIONS or parse_timestamp(record.get("ts")) is None:
        return
    observed = record.get("observed") or {}
    if record.get("ok") is True and (
        record.get("no_change") is True
        or observed.get("content_changed") is False
        or observed.get("removed") is False
    ):
        return
    host = record.get("host")
    if not isinstance(host, str):
        return
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    targets = [host]
    if "." not in host:
        targets += [
            row[0]
            for row in conn.execute(
                "SELECT host FROM worker_identity_history WHERE host LIKE ?", (host + ".%",)
            )
        ]
    for target in targets:
        state = load_history(conn, hosts=[target]).get(target, {})
        event = {
            "ts": record["ts"],
            "source": record["action"],
            "override_present": record["action"] == "host.set_override",
            "override_sha256": (record.get("parameters") or {}).get("sha256"),
        }
        previous = state.get("override_write")
        if _stamp(event) > _stamp(previous):
            state["override_write"] = event
            _save(conn, host=target, state=state)
        elif (
            previous
            and _stamp(event) == _stamp(previous)
            and event["source"] == previous.get("source")
        ):
            # Older summaries retained only source and timestamp. Recover fields
            # from the matching audit event without replacing known target evidence.
            recovered = {**event, **previous}
            if recovered != previous:
                state["override_write"] = recovered
                _save(conn, host=target, state=state)


def initialize(conn: sqlite3.Connection, *, audit_log: Path) -> None:
    """Bootstrap once from recoverable evidence; replay writes idempotently thereafter."""
    from .audit import iter_audit_records

    created = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='worker_identity_history'"
        ).fetchone()
        is None
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS worker_identity_history (host TEXT PRIMARY KEY, data TEXT NOT NULL)"
    )
    # Acquire a writer lock before reading/updating summaries.
    conn.execute("UPDATE worker_identity_history SET data=data WHERE 0")
    events = [
        event
        for event in iter_audit_records(audit_log)
        if isinstance(event, dict)
        and (
            event.get("action") in OVERRIDE_ACTIONS
            or (created and event.get("action") == "host.audit")
        )
    ]
    if created:
        events.extend(
            json.loads(row[0]) for row in conn.execute("SELECT data FROM host_observations")
        )
        # Compact endpoints can recover identities whose full audit has been pruned.
        for data, first_ts, last_ts in conn.execute(
            "SELECT s.data,p.first_ts,p.last_ts FROM configuration_states s "
            "JOIN configuration_spans p ON p.state_id=s.id"
        ):
            events.extend(
                {**json.loads(data), "ts": datetime.fromtimestamp(stamp, UTC).isoformat()}
                for stamp in {first_ts, last_ts}
            )
    events.sort(key=_stamp)
    for event in events:
        if event.get("action") in OVERRIDE_ACTIONS:
            record_write(conn, record=event)
        elif isinstance(event.get("host"), str):
            record_observation(conn, record=event)


def render_history(state: dict | None, *, sha_cache: ShaInfoCache, now: datetime) -> dict[str, Any]:
    """Qualify retained source evidence using available metadata, without updating age."""
    from .commands.monitor.json_output import _freshness, build_configured_worker_type

    state = state or {}
    baseline = state.get("baseline")
    identity = collected_identity(baseline or {})
    result: dict[str, Any] = {
        **(identity or dict.fromkeys(IDENTITY_FIELDS)),
        "status": "present" if identity else "missing",
        "observation_ts": (baseline or {}).get("ts"),
        "freshness": _freshness((baseline or {}).get("ts"), now=now),
        "collection_status": "ok" if identity else "not_collected",
        "source": "host_configuration" if identity else "unknown",
        "provenance": None,
        "contradiction_status": "unknown",
        "reasons": [],
    }
    if not baseline or not identity:
        return result
    context = build_configured_worker_type(
        baseline["host"], record=baseline, sha_cache=sha_cache, now=now
    )
    # Exclude derived freshness from pinned provenance; time passing must not change its identity.
    context.pop("freshness", None)
    result["provenance"] = {
        "identity": identity,
        "observation_ts": baseline["ts"],
        "source": "host_configuration",
        "override_present": baseline["observed"].get("override_present"),
        "configured_worker_type": context,
    }
    reasons = []

    def reason(code: str, *, status: str, ts: str) -> None:
        reasons.append({"code": code, "status": status, "evidence_ts": ts})

    latest = state.get("latest_success") or baseline
    if not collected_identity(latest):
        missing = latest["observed"].get("tc_worker_identity_status") == "missing"
        reason(
            "worker_configuration_missing" if missing else "identity_collection_unusable",
            status="contradicted" if missing else "uncertain",
            ts=latest["ts"],
        )
    missing_configuration = state.get("missing_configuration")
    if missing_configuration and _stamp(missing_configuration) >= _stamp(baseline):
        if not any(r["code"] == "worker_configuration_missing" for r in reasons):
            reason(
                "worker_configuration_missing",
                status="contradicted",
                ts=missing_configuration["ts"],
            )
    conflict = state.get("observation_conflict")
    if conflict and _stamp(conflict) >= _stamp(baseline):
        reason("observation_conflict", status="uncertain", ts=conflict["ts"])
    current = build_configured_worker_type(
        latest["host"], record=latest, sha_cache=sha_cache, now=now
    )
    if current["status"] != "resolved":
        reason("configured_destination_unresolved", status="uncertain", ts=latest["ts"])
    current_identity = collected_identity(latest)
    if (
        current_identity
        and current["status"] == "resolved"
        and any(current[key] != current_identity[key] for key in ("provisioner", "worker_type"))
    ):
        reason("configured_destination_mismatch", status="contradicted", ts=latest["ts"])
    if current["application_status"] != "applied":
        reason("override_application_unconfirmed", status="uncertain", ts=latest["ts"])
    boundary = max((state.get("override_write"), state.get("override_change")), key=_stamp)
    destination_match = context["status"] == "resolved" and all(
        context[key] == identity[key] for key in ("provisioner", "worker_type")
    )
    clears = (
        _stamp(baseline) > _stamp(boundary)
        and context["application_status"] == "applied"
        and destination_match
        and (
            not boundary
            or (
                isinstance(boundary.get("override_present"), bool)
                and baseline["observed"].get("override_present") == boundary["override_present"]
                and (
                    not boundary.get("override_sha256")
                    or context["override_sha256"] == boundary["override_sha256"]
                )
            )
        )
    )
    if boundary and not clears:
        reason(
            "override_change_unverified",
            status="contradicted",
            ts=boundary.get("evidence_ts", boundary["ts"]),
        )
    result["reasons"] = reasons
    result["contradiction_status"] = (
        "contradicted"
        if any(r["status"] == "contradicted" for r in reasons)
        else "uncertain"
        if reasons
        else "none_known"
    )
    return result
