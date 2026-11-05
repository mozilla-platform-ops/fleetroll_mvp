# Web interface

The local, read-only web interface displays recorded host state using the same
row-building and filter logic as the TUI. It does not gather observations or
perform host writes. Central hosting and write actions are proposed in the
[client/server design](design/client-server-design.md), not implemented by this app.

## Run locally

From the repository root, with Python and frontend dependencies installed:

```bash
pnpm --dir web build
uv run fleetroll web
```

Open `http://localhost:23847`. The backend serves the built `web/dist/` assets
and API in one process. For frontend development, run these in separate terminals:

```bash
uv run fleetroll web --dev
pnpm --dir web dev
```

Open `http://localhost:5173`; Vite proxies `/api` to the backend. `--dev` also
allows GET requests from that origin through CORS. Use `--port` to change the
backend port and update the Vite proxy when needed.

The server defaults to `127.0.0.1` and has no authentication. Although `--host`
accepts other addresses, this app has no remote-access controls; use loopback for
the local workflow. It exposes recorded operational data and the local DB path.

## Host grid

The grid polls every 30 seconds. Enter a [filter/sort query](monitor-filter-sort.md)
and press Enter or leave the input to apply it; Escape or Clear removes it.
The URL retains the filter. Saved filters come from `configs/filters/*.yaml`;
Recent stores up to ten deduplicated queries in browser local storage, separately
from TUI history. Saved filters are loaded when the page fetches them; reload the
page after editing their files.

The header shows version, database, host counts, response age, stale-data state,
and log-size warnings. Response age is not collection age: inspect `DATA` for
source freshness. Override and vault cells offer full-checksum/context popovers.
See [rollout verification](rollout-health-verification.md) for Puppet/health
column meanings.

## Implementation

| Area | Source and responsibility |
| --- | --- |
| App and settings | `fleetroll/commands/web/app.py`, `entry.py`, `settings.py`: FastAPI/uvicorn, routers, development CORS, static mount |
| Readiness | `routes/health.py`: DB probe; HTTP 503 on failure |
| Host rows | `routes/hosts.py`: LocalProvider, shared monitor formatting, query validation; HTTP 400 for invalid queries |
| Saved filters | `routes/filters.py`: shared named-filter loader |
| Schemas | `schemas.py`: Pydantic response models and OpenAPI |
| Logging | `logging.py`: structured request logs and request IDs |
| Frontend | `web/src/pages/Hosts.tsx`, `lib/api.ts`, `lib/useFilterHistory.ts` |

The internal API provides `/api/hosts` (`filter` and `sort` parameters),
`/api/filters`, `/api/health`, and `/api/hello`. API handlers reuse SQLite and
monitor helpers; they do not define a supported external application API. See
[tool integrations](reference/tool-integrations.md) for supported machine interfaces.

The frontend uses React/TypeScript, Vite, Tailwind, TanStack Query/Table, and
Radix primitives. Keep requests in the typed API client, use generated response
types, and keep shared column/filter semantics aligned with the monitor. Use
semantic color tokens, readable contrast, and tabular numerals for numeric cells.
Dependency versions belong in `web/package.json` and its lockfile.

## Development checks

After backend schema changes, regenerate `web/src/lib/types.generated.ts` with
`scripts/generate-web-types.sh` and review the diff. The script starts a temporary
backend and requires a working DB readiness response, curl, and frontend tools.
Generated types are committed; include their diff with the schema change.

```bash
uv run pytest tests/web tests/integration/test_web_filters_endpoint.py -v
pnpm --dir web typecheck
pnpm --dir web lint
pnpm --dir web test
pnpm --dir web build
```

Playwright smoke tests use `pnpm --dir web test:e2e` with the configured browser
installation. Authentication (`mvp-kaw5.4`), metrics/tracing (`mvp-kaw5.5`), and
live monitor parity (`mvp-kaw5.9`) remain separate work.
