"""Persist the interactive monitor's last view for each host or host list."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class MonitorView:
    query: str = ""
    overrides_only: bool = False
    os_filter: str | None = None
    sort: str = "host"


def view_path(directory: Path, *, source: str, is_host_list: bool) -> Path:
    key = f"list:{Path(source).resolve()}" if is_host_list else f"host:{source.lower()}"
    return directory / "monitor_views" / f"{hashlib.sha256(key.encode()).hexdigest()}.json"


def load_view(path: Path) -> MonitorView:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return MonitorView()
    except (OSError, ValueError) as exc:
        log.warning("could not load monitor view: %s", exc)
        return MonitorView()

    if not isinstance(data, dict):
        return MonitorView()
    query = data.get("query")
    os_filter = data.get("os_filter")
    sort = data.get("sort")
    return MonitorView(
        query=query if isinstance(query, str) else "",
        overrides_only=data.get("overrides_only") is True,
        os_filter=os_filter if os_filter in ("L", "M", "W") else None,
        sort=sort if sort in ("host", "role", "ovr_bch") else "host",
    )


def save_view(path: Path, *, view: MonitorView) -> bool:
    temp_path = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as temp:
            temp_path = Path(temp.name)
            json.dump(asdict(view), temp)
        temp_path.replace(path)
        return True
    except OSError as exc:
        log.warning("could not save monitor view: %s", exc)
        return False
    finally:
        if temp_path is not None:
            with contextlib.suppress(OSError):
                temp_path.unlink(missing_ok=True)
