# Web Interface Design

Bead: `mvp-kaw5` (P1 epic)

A locally-running web server + browser UI for fleetroll — a browser-based analog to `host-monitor`. This document covers the initial "hello world" version plus the full scaffold so follow-up beads can add features without re-litigating stack choices.

Design decisions were informed by expert reviews of a related internal app (hangar), available in `/Users/aerickson/git/hangar/{APPLICATION,BACKEND,FRONTEND,DESIGN}_REVIEW.md`. Lessons applied are cross-referenced in [§9 Lessons-Applied Checklist](#9-lessons-applied-checklist).

---

## Table of Contents

1. [Goals & Non-Goals](#1-goals--non-goals)
2. [Architecture Overview](#2-architecture-overview)
3. [Backend](#3-backend-fleetrollcommandsweb)
4. [Frontend](#4-frontend-web)
5. [Build & Dev Workflow](#5-build--dev-workflow)
6. [Testing Strategy](#6-testing-strategy)
7. [Security Posture](#7-security-posture-v1--local-only)
8. [Observability](#8-observability)
9. [Lessons-Applied Checklist](#9-lessons-applied-checklist)
10. [Anti-Patterns We're Avoiding](#10-anti-patterns-were-avoiding)
11. [Follow-up Beads](#11-follow-up-beads)

---

## 1. Goals & Non-Goals

### Goals

- Run a local web server with `fleetroll web` and open a browser UI.
- Reuse the existing SQLite data layer (`fleetroll/db.py`, `fleetroll/commands/monitor/data.py`) — no new data access code.
- Establish a full, opinionated scaffold (backend + frontend) so later feature beads can add functionality without renegotiating stack choices.
- Bake in lessons from expert app reviews up-front rather than retrofitting them later.

### Non-Goals (v1)

- Multi-user auth or session management.
- Remote / production deployment.
- Write operations or mutating fleet state.
- WebSockets, server-sent events, or real-time push updates.
- Parity with all TUI columns and filters (that's a follow-up bead).

---

## 2. Architecture Overview

```
Browser
  │  HTTP GET /           → static SPA (React)
  │  HTTP GET /api/*      → JSON (FastAPI)
  ▼
uvicorn (single process, `fleetroll web`)
  │
FastAPI app
  ├── /api/health         → DB readiness probe
  ├── /api/hello          → hello-world endpoint
  └── /                   → serves web/dist/ (prod) or 404 hint (dev)

FastAPI handlers
  └── fleetroll/db.py              get_connection()
  └── fleetroll/commands/monitor/data.py
                                   load_latest_records()
                                   build_row_values()
  └── fleetroll/commands/monitor/types.py
                                   DataContext  ← canonical data shape
```

**Data flow (v1):**

```
browser → GET /api/hello
        → FastAPI handler
        → fleetroll.db.get_connection()  (stdlib sqlite3, no ORM)
        → Pydantic response model
        → JSON
```

**Dev-mode data flow:**

```
browser → Vite dev server (localhost:5173)
        → /api/* proxy → FastAPI (localhost:8765)
        → same handler chain
```

The backend is a **thin HTTP layer only**. No new data access logic lives in `fleetroll/commands/web/` — all reads go through existing helpers.

---

## 3. Backend (`fleetroll/commands/web/`)

Mirrors the `monitor/` subpackage layout.

### File map

```
fleetroll/commands/web/
├── __init__.py
├── entry.py        # cmd_web() Click handler; starts uvicorn
├── app.py          # create_app() FastAPI factory; middleware; static mount
├── schemas.py      # Pydantic BaseModel classes (every endpoint uses response_model=)
├── settings.py     # pydantic-settings WebSettings class
├── logging.py      # structlog setup + request-ID middleware
├── static.py       # serve web/dist/; 404 hint when missing
└── routes/
    ├── __init__.py
    ├── health.py   # GET /api/health
    └── hello.py    # GET /api/hello
```

### CLI wiring

Add one entry in `fleetroll/cli.py`:

```python
@main.command("web")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8765, show_default=True)
@click.option("--dev", is_flag=True, help="Enable CORS for Vite dev server")
def cmd_web(host: str, port: int, dev: bool) -> None:
    """Start the fleetroll web interface."""
    from fleetroll.commands.web.entry import run
    run(host=host, port=port, dev=dev)
```

### Key modules

**`entry.py`** — calls `uvicorn.run(create_app(...), host=host, port=port)`. Blocks until Ctrl-C.

**`app.py`** — `create_app(settings)`:
- Instantiates `FastAPI(title="fleetroll", version=<from __version__>)`.
- Registers request-ID middleware (from `logging.py`).
- Adds CORS middleware only when `settings.dev=True`, allowing `http://localhost:5173` only.
- Includes routers: `health`, `hello`.
- Mounts static files via `static.py` at `/`.
- Leaves a clearly marked hook comment for future Prometheus/OTel middleware.

**`schemas.py`**

```python
class HealthResponse(BaseModel):
    ok: bool
    db_ok: bool
    version: str

class HelloResponse(BaseModel):
    message: str
    version: str
    db_ok: bool
```

Every response uses `response_model=` so FastAPI validates output and generates accurate OpenAPI.

**`settings.py`**

```python
class WebSettings(BaseSettings):
    web_host: str = "127.0.0.1"
    web_port: int = 8765
    web_dev: bool = False

    model_config = SettingsConfigDict(env_prefix="FLEETROLL_")
```

**`routes/health.py`** — `GET /api/health`

Opens a DB connection, runs `SELECT 1`, closes it. Returns `200 HealthResponse(ok=True, db_ok=True)` on success; `503` on DB failure. This is a **real readiness probe** — not a liveness no-op.

**`routes/hello.py`** — `GET /api/hello`

Same DB ping logic; returns `HelloResponse(message="Hello, fleetroll", version=..., db_ok=...)`.

### New runtime dependencies

Add to `pyproject.toml` `[project.dependencies]`:

```
fastapi>=0.115
uvicorn[standard]>=0.30
pydantic-settings>=2.0
```

`pydantic` itself arrives as a `fastapi` transitive dep. No ORM, no migration tool (we use stdlib `sqlite3` via `fleetroll/db.py`).

---

## 4. Frontend (`web/`)

Top-level sibling directory — entirely separate from the Python package.

### Tech stack

| Layer | Choice | Rationale |
|---|---|---|
| Build | Vite 5 | Fast HMR, Rollup output, first-class TS/React support |
| UI framework | React 19 | Matches hangar praise; component ecosystem; required for TanStack Table |
| Language | TypeScript (strict) | Strict mode from day one — non-strict was an explicit hangar anti-pattern |
| Styling | Tailwind 3.x | Closed token set in config — see below |
| Data fetching | TanStack Query v5 | No raw `useEffect + fetch` — an explicit hangar anti-pattern |
| Tables | TanStack Table v8 | For the eventual host grid; included in scaffold so layout decisions aren't re-opened |
| Primitives | Radix UI | `@radix-ui/react-dialog`, `@radix-ui/react-dropdown-menu` — a11y-correct (focus trap, aria, ESC dismiss) |
| Class utilities | clsx + tailwind-merge | `cn()` helper in `src/lib/cn.ts` |
| Fonts | DM Sans + DM Mono | Praised in hangar review as appropriate for technical dashboards |

### File map

```
web/
├── package.json
├── tsconfig.json          # "strict": true
├── vite.config.ts         # dev proxy: /api → localhost:8765
├── tailwind.config.ts     # closed color token set
├── postcss.config.js
├── index.html
└── src/
    ├── main.tsx
    ├── App.tsx             # router root; ErrorBoundary; QueryClientProvider
    ├── ErrorBoundary.tsx   # root error boundary (hangar review: required)
    ├── index.css           # Tailwind layers + CSS custom properties for tokens
    ├── lib/
    │   ├── api.ts          # typed fetch wrapper over generated types
    │   ├── cn.ts           # cn() = clsx + twMerge
    │   └── types.generated.ts  # output of openapi-typescript (committed)
    ├── components/
    │   ├── Button.tsx      # scaffold (empty, typed props)
    │   ├── Badge.tsx       # scaffold — used immediately for DB health indicator
    │   └── Dialog.tsx      # scaffold (wraps Radix Dialog)
    └── pages/
        └── Hello.tsx       # v1 page: fetches /api/hello, renders greeting + Badge
```

### Tailwind color token set (`tailwind.config.ts`)

All semantic color references go through named tokens — **no hex literals in components**, **no raw Tailwind palette names sprinkled across files** (explicit hangar anti-pattern).

```ts
colors: {
  brand: { /* scale: 50–900 */ },
  status: {
    online:  "#...",  // host healthy
    warn:    "#...",  // degraded
    crit:    "#...",  // unhealthy / puppet fail
    idle:    "#...",  // no recent data
    unknown: "#...",
  },
},
```

### Type scale

Defined as Tailwind utilities, not ad-hoc `text-[10px]` overrides:

| Token | Size | Use |
|---|---|---|
| `text-display` | 24px | Page title |
| `text-heading` | 16px | Section headers |
| `text-body` | 14px | Default prose |
| `text-caption` | 12px | Labels, secondary info |
| `font-mono` | DM Mono | Hostnames, SHAs, IDs, timestamps |

`tabular-nums` applied as a Tailwind utility class on all numeric columns — prevents layout jitter as values update.

WCAG AA contrast requirement: `text-caption` on any dark surface must use `text-gray-400` or lighter, never `text-gray-600/700` (explicit hangar finding).

### Routing (v1: single route)

```tsx
// App.tsx
<BrowserRouter>
  <ErrorBoundary>
    <Routes>
      <Route path="/" element={<Hello />} />
      <Route path="*" element={<NotFound />} />  {/* required — hangar review */}
    </Routes>
  </ErrorBoundary>
</BrowserRouter>
```

### Data fetching

TanStack Query wraps all API calls — no raw `useEffect + useState + fetch`:

```tsx
// pages/Hello.tsx
const { data, isError } = useQuery({
  queryKey: ["hello"],
  queryFn: () => api.hello(),
});
```

### API client (`src/lib/api.ts`)

Thin wrapper over `fetch` with typed request/response shapes drawn from `types.generated.ts`. A single `api` object is the only place `fetch` is called — consistent with hangar's praised `src/api.ts` pattern.

### Vite dev proxy (`vite.config.ts`)

```ts
server: {
  proxy: {
    "/api": "http://127.0.0.1:8765",
  },
},
```

Eliminates CORS friction in dev. In prod, FastAPI serves the built `web/dist/` directly.

---

## 5. Build & Dev Workflow

### Dev (two processes)

```bash
# Terminal 1 — backend
uv run fleetroll web --dev

# Terminal 2 — frontend
pnpm --dir web dev
# Opens http://localhost:5173
```

### Production (single process)

```bash
pnpm --dir web build          # outputs web/dist/
uv run fleetroll web          # serves API + static from web/dist/
# Opens http://localhost:8765
```

### OpenAPI type codegen

`scripts/generate-web-types.sh`:

1. Starts backend in background on a free port.
2. Fetches `/openapi.json`.
3. Pipes through `openapi-typescript` → `web/src/lib/types.generated.ts`.
4. Stops background server.

Run after any schema change. Pre-commit hook warns if `types.generated.ts` is stale (drift detected via `git diff --exit-code`). CI fails the build on drift.

---

## 6. Testing Strategy

### Backend (pytest)

New directory: `tests/web/`

| File | What it tests |
|---|---|
| `test_api_health.py` | `GET /api/health` — 200 when DB present; 503 when DB missing |
| `test_api_hello.py` | `GET /api/hello` — response shape matches `HelloResponse` schema |

Both use FastAPI's `TestClient` (synchronous, no uvicorn process needed) and the existing `tmp_dir` fixture from `tests/conftest.py` for isolated SQLite.

Register a new pytest marker in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
markers = [
  "allow_validation",
  "integration: ...",
  "tui: ...",
  "web: web interface tests",
]
```

Run web tests: `uv run pytest -m web -v`

### Frontend (Vitest + Playwright)

- **Vitest unit**: smoke test for `Hello.tsx` — mocks `/api/hello`, asserts "Hello, fleetroll" renders.
- **Playwright e2e**: single smoke test — starts backend + built frontend, loads `http://localhost:8765`, asserts heading text.

### CI gates

Before any build or deploy step (per hangar review):

```
ruff check / format
ty check
uv run pytest
pnpm --dir web typecheck
pnpm --dir web lint
pnpm --dir web test
pnpm --dir web build
openapi-typescript drift check (git diff --exit-code)
```

---

## 7. Security Posture (v1 = local only)

v1 binds to `127.0.0.1` and has no auth, but we set up correct defaults now rather than hardening later:

| Decision | Detail |
|---|---|
| Default bind | `127.0.0.1`, not `0.0.0.0`. Changing requires explicit `--host`. |
| No mutating endpoints | v1 is read-only. Any future write endpoint must land with input-whitelist validation + audit log entry (per hangar review) — enforced by code review gate on `routes/`. |
| Error handling | FastAPI default 500 handler returns generic message only. No raw tracebacks over HTTP. Debug detail gated behind `--dev` flag. |
| CORS | Allowed only for Vite dev origin (`http://localhost:5173`), only when `--dev` is set. Off by default in production. |
| Future auth plan | When the server moves off localhost, identity will be enforced in-app (JWT validation or header trust with allowlist) — not perimeter-only. This is documented here so it's a named requirement, not an afterthought. |

---

## 8. Observability

- **Structured logging**: `structlog` JSON formatter (bead `mvp-l0h` tracks adding this to the full CLI; web module adopts it first as a pilot).
- **Request-ID middleware**: generates a UUID per request, injects it into `structlog` context and the `X-Request-ID` response header. Enables log correlation.
- **Readiness**: `GET /api/health` performs a real DB check on every call. Returns `503` on failure so a future reverse proxy or health-check script can detect the process is up but data is unreachable.
- **Metrics hook**: `app.py` contains a clearly marked comment block showing where to mount a Prometheus `instrumentator` or OTel middleware when bead `web: metrics + tracing` is implemented. No-op in v1.

---

## 9. Lessons-Applied Checklist

Cross-reference of hangar review recommendations → decisions in this design.

| Hangar review finding | How we apply it here |
|---|---|
| Single source of truth for API types (hand-synced TS was an anti-pattern) | OpenAPI → `openapi-typescript` codegen; `types.generated.ts` committed and drift-checked in CI |
| `response_model=` on every FastAPI endpoint | Required in `schemas.py` + code review gate |
| Thin service layer; no god files | `routes/` split by endpoint; no file >~100 lines |
| Real readiness probe (not liveness-only) | `GET /api/health` opens DB connection, returns 503 on failure |
| Strict TypeScript from day one | `"strict": true` in `tsconfig.json`; no `any`, no `@ts-ignore` |
| TanStack Query for all data fetching | `useQuery` wraps every API call; no raw `useEffect + fetch` |
| Radix UI primitives for modals/menus | `@radix-ui/react-dialog` + `@radix-ui/react-dropdown-menu` included in scaffold |
| Error boundaries at root + `*` 404 route | `ErrorBoundary.tsx` wraps `<Routes>`; explicit `path="*"` route |
| Closed Tailwind color token set (no hex literals) | `tailwind.config.ts` defines `status-online/warn/crit/idle` + `brand` scale |
| `tabular-nums` on numeric columns | Applied via utility class; documented in type-scale section |
| WCAG AA contrast — `text-gray-600/700` fails on dark | Constraint documented in type-scale section; `text-gray-400` minimum on dark |
| `DM Sans + DM Mono` pairing | Included in Tailwind config / `index.css` |
| Typed, exhaustive API client (`src/api.ts` pattern) | `src/lib/api.ts` is the only place `fetch` is called |
| Vite dev proxy (`ws: true`) to backend | `vite.config.ts` proxy block |
| Prettier + ESLint from day one | Listed in `devDependencies` |
| CI gates before build/deploy | CI pipeline runs ruff, ty, pytest, pnpm typecheck/lint/test/build, drift check |
| Default bind to loopback | `127.0.0.1` default; `--host` required to override |
| No debug tracebacks over HTTP | FastAPI default error handler; `--dev` guards debug detail |
| CORS only in dev | Middleware conditional on `settings.dev` |
| Audit log for future write endpoints | Documented policy in §7; enforced at code review |
| `structlog` structured logging | Request-ID middleware + JSON formatter in `logging.py` |
| Separate background jobs from API process | v1 has no background jobs; future work goes in its own process |

---

## 10. Anti-Patterns We're Avoiding

Each item is from the hangar reviews with a one-line pointer to how this design avoids it.

| Anti-pattern | How we avoid it |
|---|---|
| Monolithic container mixing scheduler + API pinned to `--workers 1` | No scheduler in v1; future background work will be a separate process |
| Hand-synced TypeScript interfaces | OpenAPI codegen → `types.generated.ts` |
| Perimeter-only auth with no in-app identity check | Documented future-auth plan in §7 |
| `useEffect + useState + fetch` with no abort/retry | TanStack Query wraps all fetches |
| God files (500+ line routers, 800+ line components) | `routes/` split; `pages/` split; per-endpoint files |
| Scattered hex literals and ad-hoc Tailwind palette picks | Closed token set in `tailwind.config.ts` |
| `text-[10px]` and `text-gray-600/700` on dark surfaces | Named type-scale tokens; WCAG AA constraint documented |
| Unused dependencies | Deps listed in §4 are all used in v1 scaffold; no speculative imports |
| Duplicate helpers (e.g., `timeAgo` defined 4×) | Utilities live in `src/lib/`; one implementation per concern |
| `requests` + threads inside async FastAPI | All handlers are sync `def` (no async needed for stdlib sqlite3) or properly async — no threadpool hacks |
| Homegrown schema migration | We use existing `fleetroll/db.py` schema; no new tables in v1 |

---

## 11. Follow-up Beads

File these after `docs/web-interface.md` lands and `mvp-kaw5` is updated:

| Bead title | Summary |
|---|---|
| `web: hello-world implementation` | Implement the scaffold described in this doc (backend + frontend, v1 only) |
| `web: read-only host grid` | `GET /api/hosts` endpoint + `@tanstack/react-table` grid showing the same columns as `host-monitor` |
| `web: filter + sort parity` | Expose `fleetroll/commands/monitor/query.py` filter DSL via query params; frontend filter bar |
| `web: auth plan` | Decide between local-only-forever vs. in-app identity; document and implement |
| `web: metrics + tracing` | Wire OpenTelemetry (hook already in `app.py`); add Prometheus endpoint |
