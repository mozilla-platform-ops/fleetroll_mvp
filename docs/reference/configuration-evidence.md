# Configuration collection evidence (version 1)

Linux/macOS `gather host` adds `observed.configuration_evidence` to its JSON
result and stored observation. Monitor JSON preserves this object unchanged;
worker-evidence schema v3 and gwhc evidence have independent versions. Historical
rows without this object mean **not collected**, not absent or verified.

This contract describes collection. The separate
[verification v1](configuration-verification.md) evaluator consumes this evidence.
Existing flat fields and SSH-oriented top-level `ok`
remain available; a successful SSH audit may contain a partial or failed
configuration capture. No collection status alone establishes convergence.

## Envelope

| Field | Meaning |
| --- | --- |
| `schema_version` | Integer 1, independent of Puppet producer metadata version |
| `attempt` | Local collection attempt UUID, start/end, status/reason and transport exit |
| `snapshot` | Remote start/end, method `double_read_v1`, consistency |
| `components` | Current `puppet`, `override`, `vault` evidence |
| `retained_components` | Up to three earlier usable components for currently unavailable sources |

`attempt.status` is `completed` when all three sources were collected and
interpreted with stable reads (including explicit absence), `partial` when only
some are usable or group consistency is incomplete, and `failed` when none are
usable or transport/envelope validation fails. Reasons explain incomplete
results. `ssh_rc` is null for a local execution exception or batch expiry before
SSH; the failed attempt remains recorded. Exhausted connection failures and
intermediate retries are persisted too, subject to normal observation retention.

Local attempt timestamps use UTC with microseconds; the gather row's `ts` is
the same completion time. Remote snapshot/component times are preserved in their
own clock domain. Direct parser callers without a recorded start use a null
attempt start. A remote capture must have timezone-aware ordered bounds within
12 seconds (the collector's ten-second deadline plus scheduling tolerance).

`snapshot.consistency` is `stable`, `changed`, or `unknown`. Two full reads compare
content hashes and descriptor/path identities, size, ownership, mode, and
nanosecond modification/change times. This detects observed races; it is not a
lock or an atomic filesystem snapshot and cannot exclude an intervening
change-and-restore. A partial or changed snapshot cannot supply coherent group
proof, even if some individual files were readable.

## Components

States are attached to the three components, not to every scalar field.

| `status` | Meaning |
| --- | --- |
| `present` | File read and fingerprinted consistently; Puppet metadata also passed validation |
| `absent` | Both reads explicitly found the file absent |
| `unreadable` | Permission/IO failure or dangling symlink; never inferred absence |
| `invalid` | Malformed data, invalid types, inconsistent run result, nonregular/oversized file, or observed race |
| `unsupported` | Puppet producer metadata version is not supported |
| `not_collected` | Collector/transport unavailable, timeout, or no new-format collection |

Components carry `attempt_id`, `consistency`, `reason`, original `observed_at`
and, for stable reads, `confirmed_at`. File evidence also carries `path`, full
`sha256`, and a `file` stat fingerprint with integer device/inode/size,
mtime/ctime seconds and nanoseconds, mode, UID and GID. Successful observation
times are null for unsuccessful reads/races. Metadata interpretation errors
retain the original file-read timestamps but remain ineligible via their status.

File hashes use the original bytes, including empty files. The remote collector
does not transmit vault or override bytes. It bounds metadata to 64 KiB,
override to 1 MiB, vault to 16 MiB; oversized files are invalid. The local snapshot
envelope is limited to 128 KiB. A missing/denied Puppet Ruby collector is
`collector_unavailable`; no fallback claims a coherent capture. Windows is
`unsupported_os` in this contract.

With `gather host --no-content`, the gather skips downloading and caching the
override file. When the audit detects an override and the current snapshot has
a stable override reading, the gather reports that checksum in
`observed.override_sha256`. Otherwise the checksum is unknown.

### Puppet metadata

Supported producer format is integer `schema_version: 1`. Required fields are
timezone-aware `ts`, finite nonnegative `duration_s`, integer exit code 0–255,
and boolean `success` consistent with exit 0/2 meaning success. Optional supplied
revision/checksum/boolean/string fields must be valid or null. Git revisions
must be complete 40/64-character hexadecimal IDs and file checksums complete
64-character SHA-256 values. Missing optional fields remain missing.

The component preserves `producer_schema_version`, `fields_present` (the exact
producer key set), `raw` (the parsed object), original `content_b64`, and
`producer_ts`. `values` contains interpreted fields only for a valid supported
document, preserving supplied nulls. Malformed/unsupported metadata never borrows
values from shell KV fields, legacy YAML or previous observations.

`applied_files.override` and `.vault` describe the corresponding producer
checksum: `present` with full checksum, `not_collected` for an omitted field,
or `unknown` for null. **V1 null does not prove absence**: the writer can emit
null when hashing fails. Even two explicitly null checksums cannot establish
absence/convergence. Deployed file absence is a separate explicit observation.

`run_provenance` identifies `metadata_v1`, with null qualified `invocation_id`,
`exclusive: false`, and reason `v1_has_no_invocation_identity_contract`.
An additive reported `run_id` is preserved as `reported_run_id`, but v1 defines
no authority for it. Producer changes for explicit applied absence or invocation
identity require a separately coordinated supported contract. No new producer
deployment is required to collect v1 with these limitations.

## Retention and consumer rules

Current components always belong to the current attempt. Retained components
keep their original attempt IDs, times and provenance, and never move into the
current snapshot. There is at most one retained component per source, without
recursive envelopes. SQLite updates retention atomically with inserting the new
observation. Repeated failures preserve these facts without refreshing them.
Old rows are not rewritten, and old flat nulls never manufacture new evidence.

Consumers must check the supported version, latest attempt status, group and
component consistency, component status, field presence, source freshness and
producer limitations before using comparisons. The separate evaluator defines
verification rules; see [verification v1](configuration-verification.md). No fields
here authorize a rollout advance, reboot or write.
Legacy `PP_MATCH`, HEALTHY and Windows semantics remain separate. As a parsing
correction, legacy KV fallback is now a whole-source choice: when a
`PP_STATE_JSON` line exists, missing/null JSON fields are not filled from KV.

See the [implementation design](../design/coherent-configuration-collection.md)
and [Puppet metadata contract](puppet-state-tracking.md) for deployment context.
