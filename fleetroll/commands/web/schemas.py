"""Pydantic response models for all web endpoints."""

from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    ok: bool
    db_ok: bool
    version: str


class HelloResponse(BaseModel):
    message: str
    version: str
    db_ok: bool
