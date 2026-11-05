# Client/server architecture proposal

Central service proposal (`mvp-si5u`), not a deployed API contract. The local
DataProvider abstraction exists; central scanning, a shared API, RemoteProvider,
and web writes remain planned. See [the current web interface](../web-interface.md)
for implemented behavior.

Each operator currently gathers into local SQLite, multiplying SSH/API traffic.
The proposal runs collection centrally and serves the same observations to TUI
and browser clients through the DataProvider boundary.

## Architecture and decisions

```text
Leader scanner ──→ PostgreSQL ──→ REST + SSE ──→ read-only dashboard / TUI
                        ↑              ↑
                 shared notes     API replicas

Operator's local UI ──→ central reads or local SQLite
                    └─→ direct SSH writes using the operator's key
```

| Concern | Proposed choice |
| --- | --- |
| API | FastAPI, uvicorn, sse-starlette; REST snapshots and one-way SSE updates |
| Central storage | PostgreSQL; avoid SQLite WAL on a shared network volume |
| Local storage | Keep SQLite and LocalProvider for standalone operation |
| Deployment | Multiple Kubernetes API replicas; only the elected leader scans |
| Election | `pg_try_advisory_lock`; release on connection loss, other replicas retry |
| Inventory | Bake `configs/host-lists/all.list` into the image; redeploy to update |
| Client | RemoteProvider with httpx for REST/SSE; proposed `--server` selection |
| Central authentication | Bearer token; deployment/authentication requirements must be complete before exposure |
| Notes | Shared server-managed state |
| Host writes | Operator machine and privileged SSH key; server key permits collection only |
| Vault contents | Never stored or served centrally; retain only checksums/metadata |

One SPA would support two surfaces. Central pods serve the read-only dashboard
with host-write routes absent and `enable_writes=false` fixed by deployment.
The local operator UI would use `enable_writes=true`, refuse non-loopback binds,
and invoke existing Fleetroll command paths for writes. Vault inspection would
happen locally over the operator's SSH connection. Local loopback access and
central bearer authentication are separate trust models.

`GET /api/v1/capabilities` would report `writes` and `vault_content` capabilities
for the SPA. Hiding controls is not the enforcement mechanism: route mounting,
bind restrictions, and separate SSH keys enforce the boundary. Centralizing host
writes later would require a new delegation/key design.

## Proposed API

All routes below are design targets:

| Method/path | Purpose |
| --- | --- |
| `GET /api/v1/snapshot?hosts=...` | Initial complete client state |
| `GET /api/v1/observations/stream?hosts=...` | SSE updates |
| `GET /api/v1/tc-workers?hosts=...` | Latest TC evidence |
| `GET /api/v1/github-refs` | Branch revisions |
| `GET /api/v1/windows-pools` | Windows pool metadata |
| `GET /api/v1/notes?hosts=...` | Notes |
| `POST /api/v1/notes` | Add shared note |
| `DELETE /api/v1/notes/{hostname}` | Clear shared notes |
| `GET /api/v1/sha-info/{sha_prefix}` | Override/vault metadata |
| `GET /api/v1/overrides/{sha_prefix}` | Override content |
| `GET /api/v1/status` | Readiness and scan status |
| `GET /api/v1/capabilities` | Surface capabilities |

Local-only routes would be `POST/DELETE /api/v1/hosts/{hostname}/override`,
`POST /api/v1/hosts/{hostname}/vault`, `POST /api/v1/hosts/{hostname}/puppet-run`,
and `GET /api/v1/hosts/{hostname}/vault-content`. The central service has no vault
content endpoint. Note writes change shared metadata, not managed-host state.

## Delivery sequence

1. **DataProvider abstraction:** the protocol and LocalProvider are present in
   `fleetroll/data_provider.py`, used by monitor entry/display code.
2. **Read-only service:** introduce `fleetroll/server/` for app, scanning, events,
   and configuration; implement PostgreSQL storage, RemoteProvider, and proposed
   `fleetroll server --config server.toml` / monitor `--server` selection.
3. **Local write-capable UI (phase 2.5):** extend the existing read-only host grid
   with conditional write routes, capabilities, loopback enforcement, and thin
   adapters around override, vault, and Puppet command functions.
4. **Shared state (phase 3):** shared-note API, SHA metadata, and remote-client
   note operations.
5. **Deployment (phase 4):** Kubernetes manifests, secrets, monitoring, and
   operational validation. Keep standalone local mode available. Authentication
   is required before exposing the central API, regardless of delivery phase.

## Questions to resolve before Phase 2

The [March 2026 review](../archive/client-server-design-review.md) identified
these implementation requirements:

| Area | Required decision |
| --- | --- |
| Content store | How scanning stores override files/SHA metadata; snapshot versus lazy retrieval. Vault contents remain client-only. |
| Audit trail | Where client-write records live and how remote monitors learn about them; SSE is not audit history. |
| Snapshot | Latest/latest-successful audits, TC, GitHub, Windows pools, notes, and SHA metadata, preserving source timestamps. |
| SSE | Event types, payloads, ordering/cursors, reconnects, coalescing, and TC/reference updates. In-process events alone do not distribute changes across replicas. |
| PostgreSQL | Migration/schema, timestamp ownership, retention, and note storage; do not assume the old four-table model is current. |
| Scanning | Intervals, concurrency, SSH/bastion configuration, credentials, retry/backoff, and leader behavior. |
| Remote mode | URL/config precedence, offline behavior, and whether local scans coexist. |
| Shared notes | Concurrent add/clear semantics, clear scope, and migration from local notes. |
| HTTP execution | How existing synchronous requests-based collectors run alongside async API/scanning tasks. |
| Tests | Route/provider fixtures, PostgreSQL integration, failure handling, and TUI coverage in remote mode. |

Review [integration contracts](../reference/tool-integrations.md) before exposing
a shared external interface.
