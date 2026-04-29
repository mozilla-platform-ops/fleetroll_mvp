"""Pydantic response models for all web endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


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
