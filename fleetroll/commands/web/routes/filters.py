"""GET /api/filters — return named filters from configs/filters/*.yaml."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from fleetroll.commands.monitor.named_filters import load_named_filters
from fleetroll.commands.web.schemas import SavedFilter

router = APIRouter()

_FILTERS_DIR = Path("configs/filters")


@router.get("/api/filters", response_model=list[SavedFilter])
def filters() -> list[SavedFilter]:
    named = load_named_filters(_FILTERS_DIR)
    return [SavedFilter(name=f.name, query=f.query, description=f.description) for f in named]
