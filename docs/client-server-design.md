# FleetRoll Client/Server Conversion

## Context

Every user currently runs their own scanning (SSH to all hosts, TC API, GitHub API), writing to a local SQLite DB. The monitor TUI reads from that local DB. N users = N× the SSH connections and API calls — doesn't scale. A central server should run scanning once and serve data to all clients.

The existing architecture has a clean seam: `db.py` separates scanners from display. The monitor loads data through well-defined functions (`load_latest_records`, `load_tc_worker_data_from_db`, etc.). This is the natural point to insert an API.

## Architecture

```
 Before:  [Each User] → host-audit/tc-fetch/gh-fetch → local SQLite → host-monitor TUI

 After:   [Server *]  → host-audit/tc-fetch/gh-fetch → PostgreSQL → REST API + SSE
          [Clients]   → host-monitor TUI → HTTP/SSE → server
          [Clients]   → set-override/set-vault → SSH directly (unchanged)

          * All pods serve the API; only the elected leader runs scanning.
            Leader election via PostgreSQL advisory lock (pg_try_advisory_lock).
```

### Server (read-only scanning + API)
- **Framework**: FastAPI + uvicorn + sse-starlette
- **Storage**: PostgreSQL (server); local mode retains SQLite via `db.py`
- **Deployment**: Container on K8s, multiple replicas for HA
- **Scanning**: Background async tasks (host-audit, tc-fetch, gh-fetch); only the leader pod scans
- **Leader election**: PostgreSQL advisory lock — lock holder scans, others retry on an interval; pod death releases the lock immediately
- **Host list**: `configs/host-lists/all.list` baked into the image; redeploy to update
- **API**: REST for snapshots/queries, SSE for real-time observation streaming
- **Auth**: Simple bearer token (internal tool, small team)
- **SSH key**: Read-only — server only scans, never writes to hosts

### Client
- **DataProvider protocol**: Abstract interface over local SQLite or remote API
- **RemoteProvider**: Uses httpx for REST + SSE streaming
- **TUI**: Unchanged rendering; just swap data source via `--server` flag
- **Write ops**: Clients SSH directly (unchanged) — keeps write SSH keys with operators
- **Vault content inspection**: Client-side only — operator SSHes directly to the host using their privileged key; the server never holds vault file content

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
# NOTE: No vault content endpoint. The server stores only vault SHA256 (not
# content). Vault files contain sensitive secrets; content inspection is a
# client-side operation using the operator's privileged SSH key.
POST /api/v1/notes                         # Add operator note
DELETE /api/v1/notes/{hostname}            # Clear notes for host
GET  /api/v1/status                        # Server health + scan status
```

### Key Decisions
| Decision | Choice | Rationale |
|----------|--------|-----------|
| Real-time | SSE (not WebSocket) | Unidirectional, simpler, auto-reconnect, proxy-friendly |
| Auth | Bearer token | Internal tool, sufficient for now |
| Server DB | PostgreSQL | Required for K8s HA; SQLite WAL + network PV is unreliable |
| Local DB | SQLite (unchanged) | `db.py` / `LocalProvider` unchanged; local mode still works |
| Leader election | PostgreSQL advisory lock | No K8s RBAC needed; lock released immediately on pod death |
| Deployment | K8s, multiple replicas | All pods serve API; only leader scans |
| Host list | Baked into image | `configs/host-lists/all.list`; redeploy to update (acceptable for now) |
| HTTP client | httpx | Async, SSE support, modern |
| Write ops | Client-side SSH (unchanged) | Server SSH key stays read-only; can centralize later |
| Vault content | Client-side only, never stored on server | Vault files contain sensitive secrets; SHA256 is sufficient for the TUI; content inspection uses operator's privileged SSH key |
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
- K8s manifests (Deployment, Service, ConfigMap for host list, Secret for token + DB creds)
- Keep local mode as fallback
- (Future: consider centralizing write ops if desired)
