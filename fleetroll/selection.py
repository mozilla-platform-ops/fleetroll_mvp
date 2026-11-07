"""Select collection targets using the monitor's recorded-data query language."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import click
import yaml

from . import db
from .commands.monitor.cache import ShaInfoCache
from .commands.monitor.data import build_row_values
from .commands.monitor.query import Query, apply_query, parse_query, validate_query
from .notes import default_notes_path, load_latest_notes
from .utils import resolve_host_args


@dataclass(frozen=True)
class HostSelection:
    hosts: tuple[str, ...]
    eligible: int
    source: str


def load_filter(*, expression: str | None, path: str | None) -> Query | None:
    """Reject ambiguous/invalid selectors before performing collection."""
    if expression is not None and path is not None:
        raise click.UsageError("--filter and --filter-file are mutually exclusive")
    if path is not None:
        try:
            data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise click.UsageError(f"Could not load filter file: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("query"), str):
            raise click.UsageError(f"Filter file missing 'query' string: {path}")
        expression = data["query"]
    if expression is None:
        return None
    if not expression.strip():
        raise click.UsageError("Filter query must not be empty")
    # Monitor editing tolerates unfinished tokens; collection requires complete ones.
    for token in expression.split():
        error = validate_query(parse_query(token), token)
        if error:
            raise click.UsageError(f"Invalid filter token {token!r}: {error}")
    return parse_query(expression)


def select_hosts(host_args: tuple[str, ...], *, query: Query) -> HostSelection:
    """Freeze selection before any collection; never infer a complete inventory."""
    hosts, host_file = (
        resolve_host_args(host_args, announce_expansions=False) if host_args else (None, None)
    )
    source = str(host_file) if host_file else "explicit hosts" if host_args else "local database"
    latest, latest_ok, tc, refs, windows = {}, {}, {}, {}, {}
    db_path = db.get_db_path()
    if db_path.exists():
        conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN")
            if hosts is None:
                tables = {
                    r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                hosts = sorted(
                    {
                        row[0]
                        for table in ("host_observations", "tc_workers", "worker_identity_history")
                        if table in tables
                        for row in conn.execute(f"SELECT DISTINCT host FROM {table}")  # noqa: S608 - fixed table allowlist
                    }
                )
            hosts = sorted(set(hosts))
            latest, latest_ok = db.get_latest_host_observations(conn, hosts)
            tc = db.get_latest_tc_workers(conn, hosts)
            refs = db.get_latest_github_refs(conn)
            windows = db.get_latest_windows_pools(conn)
        finally:
            conn.close()
    hosts = sorted(set(hosts or []))
    cache = ShaInfoCache(db_path.parent / "overrides", db_path.parent / "vault_yamls")
    cache.load_all()
    notes = load_latest_notes(default_notes_path())
    rows = []
    for host in hosts:
        row = build_row_values(
            host,
            latest.get(host),
            last_ok=latest_ok.get(host),
            tc_data=tc.get(host),
            sha_cache=cache,
            github_refs=refs,
            windows_pools=windows,
            notes_data=notes,
        )
        row["_host"] = host
        rows.append(row)
    return HostSelection(
        hosts=tuple(row["_host"] for row in apply_query(rows, query)),
        eligible=len(hosts),
        source=source,
    )


def prepare_selection(
    host_args: tuple[str, ...],
    *,
    expression: str | None,
    path: str | None,
    json_output: bool = False,
) -> HostSelection | None:
    """Keep unfiltered callers unchanged, and report selection outside their stdout."""
    query = load_filter(expression=expression, path=path)
    if query is None:
        if not host_args:
            raise click.UsageError("Specify HOST_OR_FILE, --filter, or --filter-file")
        return None
    selection = select_hosts(host_args, query=query)
    click.echo(
        f"Selected {len(selection.hosts)}/{selection.eligible} hosts from {selection.source} "
        "using recorded data (may be stale).",
        err=True,
    )
    if not selection.hosts and json_output:
        click.echo('{"status": "no_matches", "results": []}')
    return selection
