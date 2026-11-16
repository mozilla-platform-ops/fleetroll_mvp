"""Recorded selection and reviewable expectations for population override removal."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import click
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .constants import STALE_DATA_THRESHOLD_SECONDS

if TYPE_CHECKING:
    from .commands.monitor.query import Query
    from .selection import HostSelection


class RemovalTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    host: str = Field(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.:@-]*$")
    override_present: bool | None
    checksum: str | None
    branch: str
    os_type: str | None
    observation_ts: str | None
    latest_attempt_ts: str | None
    latest_attempt_ok: bool | None

    def blocked_reason(self) -> str | None:
        if self.os_type not in ("Linux", "Darwin"):
            return "unsupported_or_unknown_os"
        if self.override_present is None:
            return "override_presence_unknown"
        if self.override_present and (
            self.checksum is None or re.fullmatch(r"[0-9a-fA-F]{64}", self.checksum) is None
        ):
            return "full_override_checksum_unavailable"
        if self.override_present is False and self.checksum:
            return "contradictory_override_evidence"
        return None

    def freshness(self, *, now: datetime) -> str:
        try:
            observed = datetime.fromisoformat(self.observation_ts or "")
            if observed.tzinfo is None or observed > now:
                return "unknown"
            age = (now - observed).total_seconds()
            return "stale" if age > STALE_DATA_THRESHOLD_SECONDS else "fresh"
        except ValueError:
            return "unknown"


class RemovalPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    action: Literal["host.unset_override"] = "host.unset_override"
    created_at: str
    source: str
    query: str
    candidate_count: int = Field(ge=0)
    targets: list[RemovalTarget]

    @model_validator(mode="after")
    def validate_targets(self) -> RemovalPlan:
        hosts = [target.host for target in self.targets]
        if len(set(hosts)) != len(hosts) or len(hosts) > self.candidate_count:
            raise ValueError("Duplicate targets or target count exceeds candidate count")
        stamp = datetime.fromisoformat(self.created_at)
        if stamp.tzinfo is None:
            raise ValueError("Plan creation timestamp requires a timezone")
        return self


def build_removal_plan(selection: HostSelection, *, query: Query) -> RemovalPlan:
    """Use the same SQLite snapshot and fallback as the selecting query."""
    targets = []
    for row in selection.rows:
        host = row["_host"]
        latest = selection.latest.get(host) or {}
        record = latest if latest.get("ok") else selection.latest_ok.get(host) or {}
        observed = (record.get("observed") or {}) if record.get("ok") else {}
        presence = observed.get("override_present")
        targets.append(
            RemovalTarget(
                host=host,
                override_present=presence if isinstance(presence, bool) else None,
                checksum=observed.get("override_sha256"),
                branch=row.get("ovr_branch", ""),
                os_type=observed.get("os_type"),
                observation_ts=record.get("ts"),
                latest_attempt_ts=latest.get("ts"),
                latest_attempt_ok=bool(latest.get("ok")) if latest else None,
            )
        )
    return RemovalPlan(
        created_at=datetime.now(UTC).isoformat(),
        source=selection.source,
        query=" ".join(
            [f"{c.column}{c.op}{c.value}" for c in query.conditions]
            + (
                ["sort:" + ",".join(f"{s.column}:{s.direction}" for s in query.sort_keys)]
                if query.sort_keys
                else []
            )
        ),
        candidate_count=selection.eligible,
        targets=targets,
    )


def load_removal_plan(path: Path) -> RemovalPlan:
    try:
        return RemovalPlan.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as exc:
        raise click.UsageError(f"Cannot read removal plan: {exc}") from exc


def save_removal_plan(plan: RemovalPlan, *, path: Path) -> None:
    """Refuse to overwrite a previously reviewed selection."""
    try:
        with path.open("x", encoding="utf-8") as output:
            output.write(plan.model_dump_json(indent=2) + "\n")
    except OSError as exc:
        raise click.UsageError(f"Cannot save removal plan: {exc}") from exc


def describe_removal_plan(plan: RemovalPlan) -> dict:
    now = datetime.now(UTC)
    return {
        **plan.model_dump(),
        "selected_count": len(plan.targets),
        "freshness_threshold_seconds": STALE_DATA_THRESHOLD_SECONDS,
        "targets": [
            {
                **target.model_dump(),
                "freshness": target.freshness(now=now),
                "blocked_reason": target.blocked_reason(),
            }
            for target in plan.targets
        ],
    }
