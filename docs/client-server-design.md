# FleetRoll Client/Server Conversion

## Context

Every user currently runs their own scanning (SSH to all hosts, TC API, GitHub API), writing to a local SQLite DB. The monitor TUI reads from that local DB. N users = N× the SSH connections and API calls — doesn't scale. A central server should run scanning once and serve data to all clients.

The existing architecture has a clean seam: `db.py` separates scanners from display. The monitor loads data through well-defined functions (`load_latest_records`, `load_tc_worker_data_from_db`, etc.). This is the natural point to insert an API.

## Architecture

```
 Before:  [Each User] → gather-host/gather-tc/gather-gh → local SQLite → host-monitor TUI

 After:   [Server pods *] → gather-host/gather-tc/gather-gh → PostgreSQL → REST + SSE
                                                                       │
                                       ┌───────────────────────────────┤
                                       ▼                               ▼
                            [Central read-only UI]         [host-monitor TUI clients]
                                                                       ▲
          [Operator] → fleetroll web (loopback) → [Local write-capable UI]
                                                  ↳ reads:  central API (or local SQLite)
                                                  ↳ writes: SSH direct to hosts

          * All pods serve the read API; only the elected leader runs scanning.
            Leader election via PostgreSQL advisory lock (pg_try_advisory_lock).
            Server pods carry a read-only SSH key; write routes are never mounted.
```

### Server (read-only scanning + API)
- **Framework**: FastAPI + uvicorn + sse-starlette
- **Storage**: PostgreSQL (server); local mode retains SQLite via `db.py`
- **Deployment**: Container on K8s, multiple replicas for HA
- **Scanning**: Background async tasks (gather-host, gather-tc, gather-gh); only the leader pod scans
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

### Web UI topology — split by capability

One SPA codebase, two deployment surfaces with different capabilities:

- **Central dashboard (read-only)** — served from the server pods. Shared URL any team member hits. Reads via REST + SSE. `enable_writes=false` at startup; write routes are never mounted. Vault content is shown only as SHA256 with an "open locally" hint, never as file bytes.
- **Local operator UI (write-capable)** — `fleetroll web` on the operator's machine, bound to `127.0.0.1:<port>`. Reuses the same SPA assets; runs with `enable_writes=true`. Reads either proxy to the central server (`--server`) or come from local SQLite. Writes shell out through the existing CLI code paths using the operator's SSH key. Vault content inspection happens here, never on the server.

Invariants:
- Write routes exist **only** on the local surface. The server's `enable_writes` flag is `false` and non-overridable in the deployed image.
- The local UI refuses to start with `enable_writes=true` on any bind other than a loopback address.
- Loopback binding is the auth story for the local UI — it matches the existing trust model for the CLI (anyone on the operator's machine can already run the CLI). No token.
- Central server authenticates with a bearer token (see Key Decisions); the two surfaces' auth models are independent.

The SPA feature-detects capabilities at load time via `GET /api/v1/capabilities` so write-action components render only where they can function.

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
GET  /api/v1/capabilities                  # {"writes": bool, "vault_content": bool} — SPA feature detection

# Local-only routes (mounted only when enable_writes=true — i.e. `fleetroll web`
# on loopback). Never mounted on the central server deployment.
POST   /api/v1/hosts/{hostname}/override       # host-set-override
DELETE /api/v1/hosts/{hostname}/override       # host-unset-override
POST   /api/v1/hosts/{hostname}/vault          # host-set-vault
POST   /api/v1/hosts/{hostname}/puppet-run     # host-run-puppet
GET    /api/v1/hosts/{hostname}/vault-content  # inspect vault via operator SSH key
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
| Write ops | Separate process, separate machine, separate key | Writes run from the operator's machine with the operator's privileged SSH key; the server pod's SSH key is read-only. This is a security boundary enforced by key material, not just by convention. Centralizing later would require a new key/delegation story. |
| Vault content | Client-side only, never stored on server | Vault files contain sensitive secrets; SHA256 is sufficient for the TUI; content inspection uses operator's privileged SSH key |
| Web UI topology | Split: central read-only + local write-capable | Keeps the server's read-only invariant intact; loopback binding is sufficient auth for the local UI; vault content never leaves the operator machine |
| Local UI auth | Loopback-only bind, no token | Matches existing CLI trust model (operator's machine, operator's account); refuses to start on non-loopback bind when writes are enabled |
| Notes | Server-managed | Notes are shared state, makes sense on server |

## Incremental Migration (4 Phases)

### Phase 1: DataProvider Abstraction (pure refactor, no server)
- Create `DataProvider` protocol in `fleetroll/data_provider.py`
- Implement `LocalProvider` wrapping existing `db.py` functions
- Refactor `MonitorDisplay` and `entry.py` to use `DataProvider`
- All existing behavior unchanged; existing tests still pass

**Files to create/modify:**
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

### Phase 2.5: Local write-capable web UI

Once the central read-only server and `DataProvider` exist, promote the existing
`fleetroll/commands/web/` app from hello/health into the operator's write-capable
UI. Central deployment stays read-only.

- Add `enable_writes: bool` to the web app factory in `fleetroll/commands/web/`
- Mount the write routes (see API section) conditionally on `enable_writes`
- Default bind `127.0.0.1:<port>`; hard-refuse to start with `enable_writes=true`
  on any non-loopback address
- Add `GET /api/v1/capabilities` so the SPA can feature-detect
- SPA gains write-action components (set/unset override, set vault, run puppet,
  view vault content) — all gated on `capabilities.writes`
- Write route handlers wrap the existing command functions
  (`fleetroll/commands/set.py`, `vault.py`, `puppet.py`) — no duplicated SSH logic

**Files to create/modify:**
- `fleetroll/commands/web/app.py` — add `enable_writes` to the factory, write-route mounting
- `fleetroll/commands/web/routes_write.py` — new; thin wrappers around existing command functions
- `fleetroll/commands/web/routes_read.py` — new; shared read routes (same module mounted on both central and local)
- SPA under `web/` — capability-gated write-action components

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
