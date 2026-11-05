# Monitor JSON worker evidence (version 1)

`fleetroll host monitor HOST_OR_FILE --once --json` emits a host-keyed object.
Each value preserves the latest raw host audit, including `observed.role` as
the Puppet role, and adds `worker_evidence_schema_version: 1` plus three objects.
A host without an audit now has an object with `host` and explicit missing
evidence instead of `null`. Existing audit fields are unchanged when present.
All timestamps are the original source timestamps; generating a report does not
refresh observations or query Taskcluster.

Without `--once`, JSON emits the same initial host-keyed object, followed by
one complete per-host object per update (including its `host` field), as before.
The objects now have the same evidence contract in snapshots and updates.
Updates occur on host audit changes, TC-only changes, override metadata changes,
and transitions to stale evidence. Polling runs every half second and can coalesce
intermediate observations; this is a latest-state feed, not an event audit trail.
Snapshot and follow output retain their existing different outer framing.

## Configured worker type

`configured_worker_type` describes intent recorded by the latest host audit:

- `provisioner`, `worker_type`: the role default with a literal active
  `WORKER_TYPE_OVERRIDE` applied. Resolution reuses gather-tc's role mapping and
  override precedence, independently of the host-observed identity.
- `status`: `resolved` or `unresolved`; `source`: `override`, `role_default`, or
  `unknown`. `reason` is null on resolution, otherwise `host_audit_missing`,
  `host_audit_failed`, `override_metadata_unavailable`, or `unmapped_role`.
  Missing/unparseable stored override metadata prevents role fallback.
- `host_audit_status`: `ok`, `failed`, or `missing`; `observation_ts` and
  `freshness` qualify the source observation.
- `override_sha256`: the deployed override content SHA, if observed.
- `application_status`: `applied` requires matching deployed/applied override
  SHAs and a successful Puppet run; `pending` means the SHAs differ; `failed`
  means the recorded Puppet run failed; otherwise `unknown`. For no override,
  a successful run with a null/empty applied SHA counts as applied. This checks
  override convergence, not complete host health or current branch revision.

An unresolved pool has null provisioner and worker type. Failed audits do not
fall back to an older successful audit. Cached metadata only supports the literal
override assignments supported by gather-tc; shell expressions are never executed.
Unmapped roles (including Windows roles without a static mapping) remain unresolved
unless an override supplies the pool. Host-observed and TC evidence remain separate.

## Host-observed identity

`observed_worker_identity` contains `provisioner`, `worker_type`, `worker_group`,
and `worker_id` from the host configuration, with `observation_ts`, `freshness`,
and `host_audit_status`. This does not establish that a worker process is running.

`collection_status` preserves the collector's status verbatim (for example `ok`,
`missing`, or `invalid`); older observations use `not_collected`. `status` is
`resolved` only when the latest audit succeeded, collection status is `ok`, and
all four identity fields are present. No role-derived fallback fills this identity.
Partial evidence remains visible with `status: unresolved`.
`configured_pool_match` is `match`, `mismatch`, or `unknown`.

## Taskcluster evidence

`taskcluster.identity` contains the provisioner, worker type, group, and worker
ID queried for the stored successful worker observation. `observation_ts` is when
that observation was collected; `last_date_active` is the independent TC activity
timestamp. `record` preserves the complete stored TC record, including task and
quarantine fields, or is null.

- `record_status`: `stored_success` or `missing`.
- `latest_lookup_status`: always `unknown` in version 1. Failed lookups, missing
  workers, and unresolved identities are **not persisted** by gather-tc; they leave
  the last successful TC record unchanged. A stored success is not proof that the
  latest gather succeeded, even if its timestamp is fresh. A missing record is not
  evidence of inactivity. Consumers requiring a successful latest lookup must
  check the gather command's exit status as well.
- `configured_pool_match`: compares provisioner and worker type to intent.
- `observed_identity_match`: compares all four identity fields to valid
  host-collected identity. Different groups/IDs count as mismatches even within
  the same pool. Missing or unresolved inputs yield `unknown`.

Each evidence object's `freshness` is `fresh` for an observation age under one
hour, `stale` at or beyond one hour, or `unknown` for missing, invalid, naive, or
future timestamps. This describes collection age, not recent task activity.
Pool matching is independent of freshness: a stale record can still match, and
a fresh record can describe another pool. Old/mismatched records retain their
original identity and timestamps.

## Example and consumer migration

An abbreviated applied override example:

```json
{
  "ms017.example.test": {
    "host": "ms017.example.test",
    "ts": "2026-11-05T04:00:00Z",
    "ok": true,
    "observed": {"role": "gecko_t_linux_2404_talos"},
    "worker_evidence_schema_version": 1,
    "configured_worker_type": {
      "worker_type": "gecko-t-linux-talos-2404-tc-v115",
      "source": "override",
      "status": "resolved",
      "application_status": "applied"
    },
    "observed_worker_identity": {
      "worker_type": "gecko-t-linux-talos-2404-tc-v115",
      "collection_status": "ok",
      "status": "resolved",
      "configured_pool_match": "match"
    },
    "taskcluster": {
      "identity": {"worker_type": "gecko-t-linux-talos-2404-tc-v115"},
      "observation_ts": "2026-11-05T04:00:00Z",
      "last_date_active": "2026-11-05T03:59:00Z",
      "record_status": "stored_success",
      "latest_lookup_status": "unknown",
      "freshness": "fresh",
      "configured_pool_match": "match",
      "observed_identity_match": "match"
    }
  }
}
```

If the override is deployed but not applied and the worker still uses the base
pool, configured intent stays `...-tc-v115`, application becomes `pending`, and
the host identity's configured-pool match becomes `mismatch`. A TC observation
from that base pool also has `configured_pool_match: mismatch`, even if it is
fresh. If host identity collection fails, its status becomes `unresolved` and
TC's observed-identity match becomes `unknown`; the stored TC evidence stays visible.

Automation should check the version, accept additional fields, and handle a
missing-audit object instead of assuming every value is either a successful audit
or null. Stop deriving worker pools from `observed.role`. Use configured intent
and the full host identity, then require matching TC evidence and suitable source
freshness. Evaluate `last_date_active` separately and handle null activity as
unknown. Do not infer inactivity in the intended pool from missing, stale, failed,
or mismatched evidence. These fields supply evidence, not reset authorization;
Medic's reset policy is outside this contract.
