"""Deterministic role-percentage planning using recorded deployment evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import click
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .commands.monitor.query import Query
from .override_removal import RemovalTarget, build_removal_plan
from .utils import natural_sort_key

if TYPE_CHECKING:
    from .selection import HostSelection


class PercentageHost(RemovalTarget):
    role: str | None
    pending_write_ts: float | None = Field(default=None, allow_inf_nan=False)

    def awaiting_verification(self) -> bool:
        if self.pending_write_ts is None:
            return False
        try:
            stamp = datetime.fromisoformat(self.observation_ts or "")
            # Write events use whole seconds; equal-second observations cannot
            # establish that collection followed the write.
            return stamp.tzinfo is None or int(self.pending_write_ts) >= int(stamp.timestamp())
        except ValueError:
            return True


class PercentagePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    action: Literal["host.set_override_percentage"] = "host.set_override_percentage"
    created_at: str
    source: str
    role: str = Field(pattern=r"^[a-zA-Z0-9_]+$")
    target_percent: float = Field(ge=0, le=100, allow_inf_nan=False)
    checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope: list[PercentageHost]
    additions: list[str]

    @model_validator(mode="after")
    def validate_selection(self) -> PercentagePlan:
        stamp = datetime.fromisoformat(self.created_at)
        if stamp.tzinfo is None:
            raise ValueError("Plan timestamp requires a timezone")
        hosts = [h.host for h in self.scope]
        if len(set(hosts)) != len(hosts):
            raise ValueError("Duplicate scope hosts")
        if self.additions != summarize_percentage(self)["additions"]:
            raise ValueError("Additions do not match the deterministic percentage selection")
        return self


def summarize_percentage(plan: PercentagePlan) -> dict:
    """Keep the original denominator and selection stable when a plan is reloaded."""
    at = datetime.fromisoformat(plan.created_at)
    population, matching, eligible, excluded, unknown, outside, pending = [], [], [], [], [], [], []
    for host in sorted(plan.scope, key=lambda h: (natural_sort_key(h.host), h.host)):
        if host.role is None:
            unknown.append(host.host)
            continue
        if host.role.lower() != plan.role.lower():
            outside.append(host.host)
            continue
        population.append(host.host)
        if host.awaiting_verification():
            pending.append(host.host)
            excluded.append({"host": host.host, "reason": "write_awaiting_verification"})
            continue
        if host.override_present is True and (host.checksum or "").lower() == plan.checksum:
            matching.append(host.host)
            continue
        reason = host.blocked_reason()
        if reason is None and host.override_present:
            reason = "conflicting_override"
        if reason is None and host.latest_attempt_ok is not True:
            reason = "latest_collection_failed"
        if reason is None and host.freshness(now=at) != "fresh":
            reason = "observation_" + host.freshness(now=at)
        if reason:
            excluded.append({"host": host.host, "reason": reason})
        else:
            eligible.append(host.host)
    target = int(
        (Decimal(str(plan.target_percent)) * len(population) / 100).to_integral_value(
            rounding=ROUND_CEILING
        )
    )
    needed = max(target - len(matching), 0)
    additions = [] if pending and needed else eligible[:needed]
    status = (
        "no_matches"
        if not population
        else "target_exceeded"
        if len(matching) > target
        else "verification_required"
        if pending and needed
        else "insufficient_eligible_hosts"
        if needed > len(eligible)
        else "ready"
        if additions
        else "target_met"
    )
    return {
        "status": status,
        "population": population,
        "denominator": len(population),
        "recorded_matching": matching,
        "target_count": target,
        "needed": needed,
        "eligible": eligible,
        "additions": additions,
        "controls": eligible[len(additions) :],
        "excluded": excluded,
        "unknown_membership": unknown,
        "outside_role": outside,
        "writes_awaiting_verification": pending,
    }


def build_percentage_plan(
    selection: HostSelection, *, role: str, percent: float, checksum: str
) -> PercentagePlan:
    evidence = build_removal_plan(selection, query=Query())
    rows = {row["_host"]: row for row in selection.rows}
    hosts = []
    for target in evidence.targets:
        value = rows[target.host].get("role")
        hosts.append(
            PercentageHost(
                **target.model_dump(),
                role=value if value not in ("", "-", "?", "missing") else None,
                pending_write_ts=selection.override_writes.get(
                    target.host, selection.override_writes.get(target.host.split(".")[0])
                ),
            )
        )
    # Calculate the deterministic selection before validating the complete plan.
    draft = PercentagePlan.model_construct(
        created_at=evidence.created_at,
        source=evidence.source,
        role=role,
        target_percent=percent,
        checksum=checksum,
        scope=hosts,
        additions=[],
    )
    return PercentagePlan.model_validate(
        {
            **draft.model_dump(),
            "additions": summarize_percentage(draft)["additions"],
        }
    )


def describe_percentage_plan(plan: PercentagePlan) -> dict:
    now = datetime.now(UTC)
    return {
        **plan.model_dump(),
        **summarize_percentage(plan),
        "scope": [
            {**host.model_dump(), "freshness": host.freshness(now=now)} for host in plan.scope
        ],
    }


def save_percentage_plan(plan: PercentagePlan, *, path: Path) -> None:
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(plan.model_dump_json(indent=2) + "\n")
    except OSError as exc:
        raise click.UsageError(f"Cannot save percentage plan: {exc}") from exc


def load_percentage_plan(path: Path) -> PercentagePlan:
    try:
        return PercentagePlan.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as exc:
        raise click.UsageError(f"Cannot read percentage plan: {exc}") from exc


def percentage_preflight(plan: PercentagePlan) -> str | None:
    """Refuse changed recorded state and stale additions before any remote write."""
    from .selection import select_hosts

    status = summarize_percentage(plan)["status"]
    if status in ("insufficient_eligible_hosts", "verification_required"):
        return status
    if not plan.additions:
        return None
    current = build_percentage_plan(
        select_hosts(tuple(h.host for h in plan.scope), query=Query()),
        role=plan.role,
        percent=plan.target_percent,
        checksum=plan.checksum,
    )
    before = {h.host: h for h in plan.scope}
    now = datetime.now(UTC)
    for host in current.scope:
        original = before[host.host]
        fields = ("role", "override_present", "checksum", "os_type", "latest_attempt_ok")
        progressed = (
            host.host in plan.additions
            and host.role == original.role
            and host.os_type == original.os_type
            and host.latest_attempt_ok is True
            and host.override_present is True
            and (host.checksum or "").lower() == plan.checksum
        )
        if not progressed and any(getattr(original, key) != getattr(host, key) for key in fields):
            return f"recorded_population_changed:{host.host}"
        if host.host not in plan.additions and host.awaiting_verification():
            return f"recorded_population_changed:{host.host}"
        if host.host in plan.additions and host.freshness(now=now) != "fresh":
            return f"addition_evidence_expired:{host.host}"
    return None
