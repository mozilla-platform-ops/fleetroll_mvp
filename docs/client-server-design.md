# FleetRoll Client/Server Conversion

## Context

Every user currently runs their own scanning (SSH to all hosts, TC API, GitHub API), writing to a local SQLite DB. The monitor TUI reads from that local DB. N users = N× the SSH connections and API calls — doesn't scale. A central server should run scanning once and serve data to all clients.

The existing architecture has a clean seam: `db.py` separates scanners from display. The monitor loads data through well-defined functions (`load_latest_records`, `load_tc_worker_data_from_db`, etc.). This is the natural point to insert an API.

## Architecture

```
 Before:  [Each User] → host-audit/tc-fetch/gh-fetch → local SQLite → host-monitor TUI

 After:   [Server]    → host-audit/tc-fetch/gh-fetch → server SQLite → REST API + SSE
          [Clients]   → host-monitor TUI → HTTP/SSE → server
          [Clients]   → set-override/set-vault → SSH directly (unchanged)
```

### Server (read-only scanning + API)
- **Framework**: FastAPI + uvicorn + sse-starlette
- **Storage**: SQLite on the server (same schema, same `db.py`)
- **Scanning**: Background async tasks running host-audit, tc-fetch, gh-fetch on intervals
- **API**: REST for snapshots/queries, SSE for real-time observation streaming
- **Auth**: Simple bearer token (internal tool, small team)
- **SSH key**: Read-only — server only scans, never writes to hosts

### Client
- **DataProvider protocol**: Abstract interface over local SQLite or remote API
- **RemoteProvider**: Uses httpx for REST + SSE streaming
- **TUI**: Unchanged rendering; just swap data source via `--server` flag
- **Write ops**: Clients SSH directly (unchanged) — keeps write SSH keys with operators

### Key API Endpoints
```
GET  /api/v1/snapshot?hosts=...            # All data for initial TUI load
GET  /api/v1/observations/stream?hosts=... # SSE stream of new observations
GET  /api/v1/tc-workers?hosts=...          # Latest TC worker data
GET  /api/v1/github-refs                   # Latest branch SHAs
GET  /api/v1/windows-pools                 # Latest Windows pool data
GET  /api/v1/notes?hosts=...               # Operator notes
GET  /api/v1/sha-info/{sha_prefix}         # Override/vault SHA metadata
GET  /api/v1/overrides/{sha_prefix}        # Override file content
GET  /api/v1/vaults/{sha_prefix}           # Vault file content
POST /api/v1/notes                         # Add operator note
DELETE /api/v1/notes/{hostname}            # Clear notes for host
GET  /api/v1/status                        # Server health + scan status
```

### Key Decisions
| Decision | Choice | Rationale |
|----------|--------|-----------|
| Real-time | SSE (not WebSocket) | Unidirectional, simpler, auto-reconnect, proxy-friendly |
| Auth | Bearer token | Internal tool, sufficient for now |
| DB | Keep SQLite on server | Already works, WAL handles concurrent access |
| HTTP client | httpx | Async, SSE support, modern |
| Write ops | Client-side SSH (unchanged) | Server SSH key stays read-only; can centralize later |
| Notes | Server-managed | Notes are shared state, makes sense on server |

## Incremental Migration (4 Phases)

### Phase 1: DataProvider Abstraction (pure refactor, no server) ✅
- Created `DataProvider` protocol in `fleetroll/data_provider.py`
- Implemented `LocalProvider` wrapping existing `db.py` functions
- Refactored `MonitorDisplay` and `entry.py` to use `DataProvider`
- All existing behavior unchanged; existing tests still pass

**Files modified:**
- `fleetroll/data_provider.py` — new file: protocol + LocalProvider
- `fleetroll/commands/monitor/entry.py` — use LocalProvider, provider selection
- `fleetroll/commands/monitor/display.py` — accept DataProvider instead of db conn

### Phase 2: Server with Read-Only API
- New `fleetroll/server/` package (app.py, scanner.py, events.py, config.py)
- FastAPI app with read endpoints + SSE stream
- Background scanning tasks (extract from existing command logic)
- `RemoteProvider` implementation using httpx
- `--server` flag on `host-monitor`
- New CLI: `fleetroll server --config server.toml`

**New files:**
- `fleetroll/server/__init__.py`
- `fleetroll/server/app.py` — FastAPI routes
- `fleetroll/server/scanner.py` — Background scan loops
- `fleetroll/server/events.py` — In-process event bus for SSE
- `fleetroll/server/config.py` — Server config loading

### Phase 3: Notes + Shared State
- Notes CRUD endpoints on server
- Clients read/write notes via API when `--server` is set
- SHA info cache served from server

### Phase 4: Polish + Deployment
- Token auth middleware
- Server health checks / monitoring
- Deployment config (systemd unit or similar)
- Keep local mode as fallback
- (Future: consider centralizing write ops if desired)

## Open Questions
- Server deployment: bare metal, container, systemd?
- Should the server manage host lists, or do clients still specify them?
- Do we want multiple server instances (HA) or is single instance fine?
