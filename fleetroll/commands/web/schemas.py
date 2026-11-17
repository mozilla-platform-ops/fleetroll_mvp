"""Pydantic response models for all web endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    ok: bool
    db_ok: bool
    version: str


class HelloResponse(BaseModel):
    message: str
    version: str
    db_ok: bool


class SavedFilter(BaseModel):
    name: str
    query: str
    description: str


class HostRow(BaseModel):
    status: str
    host: str
    uptime: str
    override: str
    role: str
    os: str
    sha: str
    vlt_sha: str
    ovr_branch: str = ""
    ovr_sha_full: str = ""
    vlt_sha_full: str = ""
    vlt_mnemonic: str = ""
    vlt_symlink: str = ""
    mtime: str
    err: str
    tc_quar: str
    tc_act: str
    tc_j_sf: str
    pp_last: str
    pp_sha: str
    pp_exp: str
    pp_match: str
    healthy: str
    data: str
    note: str
    gwhc_worker: str = "unknown"
    gwhc_disk: str = "UNKNOWN"
    gwhc_disk_alert: str = "unknown"
    gwhc_age: str = "-"
    gwhc_ts: str = "-"
    gwhc_source: str = "sudo -n gwhc --json"
    config: str = "unknown"
    verify_why: str = "-"
    verify_age: str = "-"
    activity: str = "unknown"
    activity_reasons: str = "-"
    puppet_result: str = "unknown"
    revision_match: str = "unknown"
    override_match: str = "unknown"
    vault_match: str = "unknown"
    tc_lookup: str = "unknown"
    workload: str = "unknown"
    host_fresh: str = "unknown"
    reference_fresh: str = "unknown"
    tc_fresh: str = "unknown"
    verification_schema_version: int = 1
    verification: dict[str, Any] = Field(default_factory=dict)


class HostsSummary(BaseModel):
    version: str
    db_path: str
    total_hosts: int
    fqdn_suffix: str | None
    log_size_warnings: list[str]
    data_is_stale: bool


class HostsResponse(BaseModel):
    rows: list[HostRow]
    generated_at: datetime
    summary: HostsSummary
