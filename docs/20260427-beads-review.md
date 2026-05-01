# Beads Review — 2026-04-27

55 open beads reviewed. Summary of distinct work streams and next-step prioritization.

---

## Work streams

### 1. Web UI (local, write-capable) — epic `mvp-kaw5` [P1]

Browser-based analog to host-monitor, loopback-bound, operator's SSH key, write-capable.

- `mvp-kaw5.2` web: read-only host grid — **next, ready**
- `mvp-kaw5.3` filter + sort parity
- `mvp-kaw5.4` auth plan
- `mvp-kaw5.5` metrics + tracing
- `mvp-kaw5.6` next features (drill-downs, audit log views, write actions)

### 2. Centralization / client-server — epic `mvp-si5u` [P1]

Centralize scanning behind a shared read-only API so N operators stop making N× scans. Central server + RemoteProvider on client.

- `mvp-si5u.2` Phase 2: FastAPI + Postgres + SSE + RemoteProvider — **ready, blocks 2.5/3/4/6**
- `mvp-si5u.3` Phase 2.5: split web UI — local write-capable vs central read-only (requires `kaw5.2`)
- `mvp-si5u.4` Phase 3: shared notes + SHA info on server
- `mvp-si5u.5` Phase 4: K8s deploy, bearer-token auth, monitoring
- `mvp-si5u.6` client-submitted audit observations after operator writes

### 3. host-monitor TUI polish [P2/P4]

Many small features and two P2 bugs:

**Bugs:** `mvp-ujz` (ovr_bch sorts on SHA instead of branch), `mvp-fc3` (git_branch shows HEAD on override branches)

**Display:** `mvp-gxw` TC_T_DATE, `mvp-gpke` disk usage + ALERT, `mvp-x66v` DATA sortable by TC_DATA/HOST_DATA, `mvp-hp8` PP_SUCC, `mvp-05c` ping column

**UX/navigation:** `mvp-lsq1` v/V keys for override filtering, `mvp-26z` edit mode (e key), `mvp-265` command palette (c key), `mvp-3lk` switchable views, `mvp-4er` views vs sort modes, `mvp-2n5` OS sorting, `mvp-1ok` statistics panel, `mvp-c29` bar graph + age sort, `mvp-dov` display modes

**Help:** `mvp-5w8` filter syntax docs for negation/notes field

### 4. Override / rollouts — epic `mvp-3vc` [P4]

- `mvp-3vc.1` host-unset-override across a population
- `mvp-t8yk` selective removal by commit

### 5. Data collection / scanning [P2/P3]

- `mvp-qg2` check generic-worker is running
- `mvp-p9w` HEALTHY requires vault SHA matches last puppet run
- `mvp-2ip` RO_HEALTH uses tc job history (not just connectivity)
- `mvp-194` collect/store/display tc quarantine reason
- `mvp-5qd` SIP status on macOS workers
- `mvp-1cl` `gather` command family (gather, gather-host, gather-tc)

### 6. Inventory / external sync [P3]

- `mvp-3oz` / `mvp-ts2` ingest + sync host notes from moonshot Google Sheet
- `mvp-2ug` Mac per-group host list generator from puppet YAML
- `mvp-11w` hardware profile tracking for host pools

### 7. Naming / refactor / chores [P2/P4]

- `mvp-3fn` rename gather-tc, gather-host, gather-gh to consistent convention
- `mvp-1ea` migrate os.path → pathlib
- `mvp-l0h` structured logging with structlog
- `mvp-6rl` CLI integration tests
- `mvp-8ag` increase test coverage to 65%
- `mvp-bns` document audit log JSON schema
- `mvp-1ni` rotate notes.jsonl in maintain command

### 8. Release-notes tooling [P2]

- `mvp-ie0` `--no-orphans` and `--no-gitlog` flags
- `mvp-8v8` document release notes publication workflow

---

## Recommendation

Phase 1 of centralization (`mvp-si5u.1` DataProvider) just landed. Web hello-world (`mvp-kaw5.1`) is done. Two P1 tracks are now unlocked — a choice point.

**Suggested order:**

1. **`mvp-kaw5.2` (read-only host grid)** — smaller scope, fast feedback, validates React + DataProvider end-to-end, and `si5u.3` requires it anyway.
2. **`mvp-si5u.2` (Phase 2 central server)** — highest leverage: unblocks 2.5, 3, 4, 6. Larger lift.
3. **`mvp-si5u.3`** — merges the two tracks once both are done.

**Quick wins to slot in before `si5u.2`:**

- `mvp-3fn` rename fetch commands — rename before the server bead reuses those names.
- `mvp-ujz` (P2 bug) and `mvp-fc3` (P2 bug) — clear these before they age further.
