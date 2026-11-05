"""Bounded, read-only configuration evidence for rollout task attribution."""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .commands.monitor.cache import parse_override_file
from .constants import DEFAULT_GITHUB_REPO, DEFAULT_TC_PROVISIONER, ROLE_TO_TASKCLUSTER
from .github import parse_github_repo_url

WRITE_ACTIONS = {"host.set_override", "host.unset_override", "host.set_vault"}
BARRIER_ACTIONS = WRITE_ACTIONS | {"history.conflict"}


def parse_timestamp(value: Any) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def repository_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.replace("ssh://git@ssh.github.com:443/", "https://github.com/")
    parsed = parse_github_repo_url(value)
    return "/".join(parsed) if parsed else None


def override_info(sha: str, overrides_dir: Path) -> dict[str, Any] | None:
    """Respect extended content-address prefixes and verify the stored contents."""
    if not sha or any(char not in "0123456789abcdef" for char in sha) or len(sha) != 64:
        return None
    for path in sorted(overrides_dir.glob(f"{sha[:12]}*")):
        if not path.is_file():
            continue
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        if digest == sha:
            return parse_override_file(path)
    return None


@dataclass(frozen=True)
class WorkerIdentity:
    provisioner: str
    pool: str
    worker_group: str
    worker_id: str
    legacy: bool = field(default=False, compare=False)


def worker_identity(  # noqa: PLR0911 - distinguish each identity failure explicitly
    record: dict[str, Any], *, overrides_dir: Path, worker_group: str = "mdc1"
) -> tuple[WorkerIdentity | None, str | None]:
    observed = record.get("observed") or {}
    if not record.get("ok"):
        return None, "latest host observation failed"
    if "tc_worker_identity_status" in observed:
        identity = observed.get("tc_worker_identity")
        keys = ("provisionerId", "workerType", "workerGroup", "workerId")
        if (
            observed["tc_worker_identity_status"] != "ok"
            or not isinstance(identity, dict)
            or not all(isinstance(identity.get(key), str) and identity[key] for key in keys)
        ):
            return None, "worker identity collection failed; refresh gather host"
        return WorkerIdentity(*(identity[key] for key in keys)), None
    retained = retained_configuration(record)
    if retained:
        return retained.identity, None
    role = observed.get("role")
    mapped = ROLE_TO_TASKCLUSTER.get(role)
    sha = observed.get("override_sha256")
    info = override_info(sha, overrides_dir) if isinstance(sha, str) else None
    if observed.get("override_present") and not info:
        return None, "stored override missing or invalid; legacy pool cannot be resolved safely"
    pool_override = info.get("worker_type_override") if info else None
    if not mapped and not pool_override:
        return None, "no observed identity or legacy role/override mapping"
    provisioner, pool = mapped or (DEFAULT_TC_PROVISIONER, pool_override)
    if pool == "AUTO_under_to_dash":
        pool = str(role).replace("_", "-")
    pool = pool_override or pool
    if not isinstance(pool, str):
        return None, "legacy pool is unresolved"
    return WorkerIdentity(provisioner, pool, worker_group, record["host"].split(".")[0], True), None


@dataclass(frozen=True)
class Configuration:
    role: str
    repository: str
    branch: str
    override_sha: str | None
    git_sha: str
    vault_sha: str | None
    identity: WorkerIdentity

    @property
    def scope(self) -> tuple[str, str, str, str]:
        return self.role, self.identity.provisioner, self.identity.pool, self.identity.worker_group

    @property
    def cohort(self) -> tuple[str, ...]:
        return (*self.scope, self.repository, self.override_sha or "master")


@dataclass(frozen=True)
class Interval:
    start: dt.datetime
    end: dt.datetime
    configuration: Configuration
    source: str = "raw"
    max_gap_s: float = 0
    retention_cutoff: dt.datetime | None = None


def retained_configuration(record: dict[str, Any]) -> Configuration | None:
    """Read only the trusted proof attached by the compact SQLite reader."""
    proof = record.get("_configuration_proof")
    if not isinstance(proof, dict) or proof.get("version") != 1:
        return None
    established = parse_timestamp((proof.get("provenance") or {}).get("established_ts"))
    stamp = parse_timestamp(record.get("ts"))
    if not established or not stamp or established > stamp:
        return None
    try:
        data = dict(proof["configuration"])
        data["identity"] = WorkerIdentity(**data["identity"])
        config = Configuration(**data)
    except (KeyError, TypeError, ValueError):
        return None
    observed = record.get("observed") or {}
    if (
        config.role != observed.get("role")
        or config.repository != repository_name(observed.get("puppet_git_repo"))
        or config.git_sha != observed.get("puppet_git_sha")
        or config.override_sha != observed.get("override_sha256")
    ):
        return None
    return config


def applied_time(observed: dict[str, Any]) -> dt.datetime | None:
    timestamp = parse_timestamp(observed.get("puppet_state_ts"))
    if timestamp:
        return timestamp
    epoch = observed.get("puppet_last_run_epoch")
    if isinstance(epoch, (int, float)) and not isinstance(epoch, bool):
        try:
            return dt.datetime.fromtimestamp(epoch, tz=dt.UTC)
        except (ValueError, OverflowError, OSError):
            return None
    return None


def verified_configuration(  # noqa: PLR0911 - explicit evidence refusals remain readable
    record: dict[str, Any],
    *,
    overrides_dir: Path,
    master_refs: set[tuple[str, str]],
    worker_group: str = "mdc1",
) -> tuple[Configuration | None, str | None]:
    observed = record.get("observed") or {}
    if record.get("_compact_unverified"):
        return None, record.get("_compact_reason") or "retained configuration was unverified"
    identity, reason = worker_identity(
        record, overrides_dir=overrides_dir, worker_group=worker_group
    )
    if identity is None:
        return None, reason
    role = observed.get("role")
    if not role or observed.get("puppet_role") != role:
        return None, "applied role is missing or does not match the configured role"
    if observed.get("puppet_success") is not True or observed.get("puppet_git_dirty") is not False:
        return None, "Puppet success and a clean applied checkout are not verified"
    repo = repository_name(observed.get("puppet_git_repo"))
    git_sha = observed.get("puppet_git_sha")
    stamp, applied = parse_timestamp(record.get("ts")), applied_time(observed)
    if not repo or not isinstance(git_sha, str) or not git_sha or not stamp or not applied:
        return None, "applied repository, revision, or convergence timestamp is missing"
    if applied > stamp:
        return None, "Puppet convergence timestamp is later than the observation"
    retained = retained_configuration(record)
    if observed.get("override_present") is True:
        sha = observed.get("override_sha256")
        if not sha or sha != observed.get("puppet_override_sha_applied"):
            return None, "configured override does not match the applied override"
        if retained:
            branch = retained.branch
        else:
            info = override_info(sha, overrides_dir)
            if not info or f"{info.get('user')}/{info.get('repo')}" != repo:
                return None, "verified stored override contents or matching repository are missing"
            branch = info["branch"]
    elif observed.get("override_present") is False:
        sha, branch = None, "master"
        if observed.get("puppet_override_sha_applied"):
            return None, "override removed but Puppet still reports the old override"
        if repo != DEFAULT_GITHUB_REPO or (
            retained is None
            and observed.get("puppet_git_branch") != "master"
            and (repo, git_sha) not in master_refs
        ):
            return None, "no evidence that the clean applied revision belongs to upstream master"
    else:
        return None, "override presence is unknown"
    vault_sha = observed.get("vault_sha256") if observed.get("vault_present") else None
    if observed.get("vault_present") and (
        not vault_sha or vault_sha != observed.get("puppet_vault_sha_applied")
    ):
        return None, "configured vault does not match the applied vault"
    return Configuration(role, repo, branch, sha, git_sha, vault_sha, identity), None


def build_history(
    records: list[dict[str, Any]],
    *,
    overrides_dir: Path,
    master_refs: set[tuple[str, str]],
    max_gap: dt.timedelta,
    worker_group: str = "mdc1",
) -> list[Interval]:
    """Infer bounded intervals, breaking continuity at contradictory/unknown evidence."""
    by_time: dict[dt.datetime, list[dict[str, Any]]] = {}
    for record in records:
        stamp = parse_timestamp(record.get("ts"))
        if stamp:
            by_time.setdefault(stamp, []).append(record)
    intervals: list[Interval] = []
    boundary: dt.datetime | None = None
    write_boundary: dt.datetime | None = None
    continuous = False
    last_break: dt.datetime | None = None
    compact_anchors = [record for record in records if record.get("_compact_span_start")]
    for stamp, entries in sorted(by_time.items()):
        if any(entry.get("action") in BARRIER_ACTIONS for entry in entries):
            if any(entry.get("action") in WRITE_ACTIONS for entry in entries):
                write_boundary = stamp
            last_break, boundary, continuous = stamp, stamp, False
            continue
        audits = [entry for entry in entries if entry.get("action", "host.audit") == "host.audit"]
        # A failed collection says nothing about the host's configuration. Keep
        # the last successful observation as the continuity anchor; the normal
        # maximum gap still bounds how long that evidence can be carried.
        audits = [entry for entry in audits if entry.get("ok") or entry.get("_compact_invalidated")]
        if not audits:
            continue
        proofs = [
            verified_configuration(
                entry,
                overrides_dir=overrides_dir,
                master_refs=master_refs,
                worker_group=worker_group,
            )[0]
            for entry in audits
        ]
        config = proofs[0]
        if config is None or any(proof != config for proof in proofs):
            last_break, boundary, continuous = stamp, stamp, False
            continue
        applied = applied_time(audits[0]["observed"])
        if applied is None or (write_boundary and applied < write_boundary):
            last_break, boundary, continuous = stamp, stamp, False
            continue
        compact_gap = None
        for entry in compact_anchors:
            span_start = parse_timestamp(entry.get("_compact_span_start"))
            span_end = parse_timestamp(entry.get("ts"))
            gap = entry.get("_compact_max_gap")
            if (
                span_start
                and intervals
                and isinstance(gap, (int, float))
                and gap <= max_gap.total_seconds()
                and span_start <= intervals[-1].end <= stamp
                and span_end is not None
                and stamp <= span_end
                and (last_break is None or last_break <= span_start)
                and verified_configuration(
                    entry,
                    overrides_dir=overrides_dir,
                    master_refs=master_refs,
                    worker_group=worker_group,
                )[0]
                == config
            ):
                compact_gap = gap
        if (
            continuous
            and intervals
            and intervals[-1].configuration == config
            and (stamp - intervals[-1].end <= max_gap or compact_gap is not None)
        ):
            previous = intervals[-1]
            intervals[-1] = replace(
                previous,
                end=stamp,
                source=("compact" if previous.source == "compact" else "raw+compact")
                if compact_gap is not None
                else previous.source,
                max_gap_s=max(
                    previous.max_gap_s,
                    compact_gap
                    if compact_gap is not None
                    else (stamp - previous.end).total_seconds(),
                ),
            )
            continue
        start = max(applied, stamp - max_gap)
        if any(
            entry.get("_compact_span_start")
            and entry.get("_compact_max_gap", 0) > max_gap.total_seconds()
            for entry in audits
        ):
            start = stamp
        if boundary and start <= boundary:
            start = stamp
        if intervals and start <= intervals[-1].end:
            start = stamp
        cutoff = parse_timestamp(audits[0].get("_compact_retention_cutoff"))
        intervals.append(Interval(start, stamp, config, "compact" if cutoff else "raw", 0, cutoff))
        continuous = True
        boundary = None
    return [
        replace(item, start=max(item.start, item.retention_cutoff))
        if item.retention_cutoff
        else item
        for item in intervals
        if item.retention_cutoff is None or item.end >= item.retention_cutoff
    ]


def classify_run(
    intervals: list[Interval], *, started: dt.datetime | None, ended: dt.datetime | None
) -> tuple[Configuration | None, str | None]:
    if started is None or ended is None or ended < started:
        return None, "missing or invalid task timestamps"
    for interval in intervals:
        if interval.start <= started <= ended <= interval.end:
            return interval.configuration, None
    return None, "unknown configuration or task spans a transition/observation gap"
