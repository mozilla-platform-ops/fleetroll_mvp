# Client-Server Design Review

Review of `docs/client-server-design.md` — 2026-03-11

## What's Good

- **Clean architecture diagram** — the before/after visual is immediately clear
- **Key Decisions table** — well-reasoned, each choice has rationale
- **Vault security stance** — correct to keep vault content client-only
- **SSE over WebSocket** — right call for a unidirectional data stream
- **PostgreSQL advisory lock** — elegant leader election without K8s RBAC
- **Incremental phasing** — sensible ordering of work

## Issues and Gaps

### 1. Phase 1 is marked complete but isn't implemented ✅ fixed

The doc said Phase 1 was done ("✅") and listed specific files modified, but `data_provider.py` didn't exist and there were zero references to `DataProvider`, `LocalProvider`, or `RemoteProvider` anywhere in the codebase. Fixed by removing the checkmark, converting past tense to future tense, and renaming "Files modified" to "Files to create/modify".

### 2. Missing: Content-addressed file storage

The current system stores override files and vault YAML files locally in `~/.fleetroll/overrides/` and `~/.fleetroll/vault_yamls/` (SHA-prefix named files). The `ShaInfoCache` scans these directories to display metadata in the TUI (branch, user, repo from overrides; symlink names for vaults).

The design has `GET /api/v1/sha-info/{sha_prefix}` and `GET /api/v1/overrides/{sha_prefix}`, which addresses serving this data. But it doesn't discuss:

- How the **server** populates its content file store (during scanning? separate step?)
- Where files live on the server (local filesystem? PostgreSQL blob? object storage?)
- How `ShaInfoCache` works in remote mode — does `RemoteProvider` lazily fetch from the API, or does the snapshot endpoint include SHA metadata?

### 3. Missing: Audit log (`audit.jsonl`)

The current system has an append-only JSONL audit trail (`~/.fleetroll/audit.jsonl`) that logs every set/unset/vault operation. The design doesn't mention:

- Does the server maintain a centralized audit log?
- How do clients access audit history?
- Write operations (set-override, set-vault) remain client-side — where does the audit record go? Only locally? Both locally and POSTed to server?

This matters because currently the `AuditLogTailer` in `data.py` tails this file for live updates in the TUI. In server mode, SSE replaces this, but the audit trail itself is a separate concern.

### 4. Missing: Snapshot endpoint payload definition

`GET /api/v1/snapshot?hosts=...` is the critical initial-load endpoint. The doc doesn't specify what it returns. Given the current data loading in the monitor, it needs to bundle:

- Latest observation per host + latest ok=1 per host
- TC worker data
- GitHub refs
- Windows pools
- Notes
- SHA info cache data (override metadata, vault symlink names)

Even a rough JSON shape would help clarify the scope.

### 5. Missing: SSE event format

`GET /api/v1/observations/stream` is mentioned but the event format isn't specified. What fields? Is it the same JSON blob as stored in `host_observations.data`? Are TC worker updates and GitHub ref updates also streamed, or only host observations?

### 6. Missing: PostgreSQL schema

The SQLite schema is well-defined in `db.py`. The doc should at least note the migration strategy:

- Direct port of the 4 tables?
- Any schema changes (e.g., adding server-managed timestamps, sequence IDs for SSE cursoring)?
- Notes storage: the doc says "server-managed" in Phase 3, but current notes are in a project-relative `data/notes.jsonl` — is notes storage moving to PostgreSQL?

### 7. Missing: Scanning configuration

The server runs `host-audit`, `tc-fetch`, `gh-fetch` as background tasks. Not addressed:

- Scan intervals (how often?)
- SSH configuration for the server (keys, bastion/ProxyJump, ports)
- Concurrency — current `host-audit` SSHes to hosts in batches; what batch size/parallelism for the server?
- Error handling — what happens when SSH to a host fails? Retry? Backoff?
- TaskCluster and GitHub API credentials on the server

### 8. Missing: `--server` flag behavior details

When a client uses `--server`, what about:

- Offline fallback — if the server is unreachable, does it fall back to local DB?
- Mixed mode — can a user run local scanning AND connect to server?
- Configuration — is the server URL a CLI flag, env var, config file, or all three?

### 9. Missing: Notes conflict resolution

Notes are currently a local JSONL file in the project directory (`data/notes.jsonl`). Moving to server-managed notes means:

- Multiple users can add/clear notes concurrently — is this last-write-wins?
- Note clear is per-host — does `DELETE /api/v1/notes/{hostname}` clear all notes or just the caller's?
- Is there a migration path for existing local notes?

### 10. Minor: `requests` vs `httpx`

The key decisions table says "httpx" for the HTTP client, but the current codebase uses `requests` for TC and GitHub API calls. The server's scanning tasks will reuse existing code — worth noting whether the server side also switches to httpx (for async) or keeps requests.

### 11. Missing: Testing strategy

No mention of how the server and remote provider will be tested:

- Unit tests for API routes?
- Integration tests (server + PostgreSQL)?
- How to test `RemoteProvider` without a running server (mock server? recorded responses?)
- Do existing TUI tests need a server mode variant?

## Suggested Additions

1. ~~**Remove or correct Phase 1 completion status**~~ (fixed)
2. **Add a "Snapshot Response Shape" section** — even rough JSON
3. **Add a "Server Configuration" section** — SSH keys, scan intervals, concurrency, API credentials
4. **Add a "Storage" section** — where override/vault content files live on the server, PostgreSQL schema notes
5. **Add a "Migration" section** — notes migration, existing local data
6. **Add a "Testing" section** — at least high-level strategy
7. **Clarify audit log handling** in server mode
8. **Specify SSE event types and format**

## Overall Assessment

The doc is a solid high-level architecture. The key decisions are well-reasoned and the phasing is pragmatic. The main gap is **operational detail** — it covers *what* but not enough *how* for someone to start implementing Phase 2 without making a lot of judgment calls. The Phase 1 completion claim is inaccurate. Adding the sections above would make this implementation-ready.
