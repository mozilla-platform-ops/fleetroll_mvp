# Configuration verification (version 1)

Monitor JSON publishes `verification_schema_version: 1` and a `verification`
object, independently of worker-evidence v3 and configuration-collection v1.
CLI text, web, queries and collection selectors project the same pure
evaluator. Evaluation reads recorded evidence; it does not gather or mutate hosts.

## Configuration, activity and workload

| Signal | States | Meaning |
| --- | --- | --- |
| Configuration | `verified`, `not_verified`, `unknown` | Successful Puppet result, full revision and applicable override/vault comparisons, qualified by collection coherence and source freshness |
| TC lookup | `ok`, `missing_worker`, `failed`, `unknown` | Latest completed lookup attempt with exact configured host identity |
| TC activity | `recent`, `not_recent`, `unknown` | Fresh, linked TC observation and known last-active time under the activity window |
| Workload | `unknown` | Pool Classifier is not integrated; no outcome inference from activity/process state |

`verified` requires all four configuration components to pass, a supported
Linux/macOS platform, a completed stable capture and current usable evidence.
`not_verified` means at least one **usable current** Puppet failure or mismatch;
other unknown requirements remain listed. Otherwise the result is `unknown`.
An unqualified/stale mismatch remains visible as `observed_state: mismatch`,
without becoming a current failure. Contradiction within a component makes that
component unknown. A known failure in a different usable component stays visible.

TC lookup/activity/workload do not enter the configuration aggregate. A verified
configuration can have unknown or old activity. Recent activity does not repair
missing configuration proof. `operation_authority` is always `not_granted`.
No verdict alone permits a write, reboot, quarantine change or rollout advance.

## Inputs and rules

The evaluator consumes [configuration collection v1](configuration-evidence.md)
from the **latest** host record. Retained components keep their original times
and can supply unqualified historical facts, never a fresh current result.
Components must belong to the current attempt, be stable and have ordered capture
times. The local attempt must link to the host record's timestamp, finish after
its start, and record successful transport. Partial capture can preserve usable
independent negative evidence, but cannot produce a positive aggregate.

Metadata is revalidated against its preserved source bytes and full checksum.
Supported producer v1 must have consistent typed result/timing fields. The
Puppet component reports `succeeded`, `failed`, or `unknown`. An old completed
run can remain the applied configuration: **run completion age is not host
collection freshness**. Invalid/future completion timestamps and completions
after the metadata observation remain unknown.
Limited producer invocation provenance remains explicit and does not establish
exclusive execution.

Revision compares complete 40/64-character Git IDs, case-insensitively. The
applied repository must match the resolved expected repository, and the producer
must explicitly report a clean working tree. Producer branch `HEAD` is allowed;
branch intent comes from the exact deployed override or the default contract.
For an override, local artifact bytes must match its full observed SHA-256 and
contain unambiguous literal repository/branch assignments. Shell expressions,
duplicate assignments, absent artifacts and checksum mismatches are unresolved.
Extended checksum filename prefixes are supported. Repository names are case
insensitive; branches are case sensitive. A missing override selects the default
`mozilla-platform-ops/ronin_puppet:master` reference; unknown override presence
does not. Expected full SHA, branch, repository, fetch time, source and artifact
checksum remain in provenance. Current GitHub references are recorded inputs,
not live branch queries.

File components compare full deployed/applied SHA-256 values, case-insensitively,
from the same capture/run evidence. Explicit deployed absence plus an applied
checksum is a mismatch. A contradictory supplied applied path makes the
comparison unknown. **Producer v1 null is ambiguous**, including with explicit
deployed absence; it never proves applied absence. Omitted fields remain missing.
Thus a no-override host may correctly have unknown configuration verification.
Both files are required for supported Linux/macOS hosts in v1; there is no
role exemption or inferred `not_applicable` state. Windows is unsupported by
this new verifier and retains its independent legacy pool-hash behavior.

## Freshness and policy

Every evaluation uses one timezone-aware UTC `evaluated_at` for all hosts in a
batch and all components. It preserves source timestamps and emits ages
separately. Naive, invalid, missing and **any future** timestamp is unknown;
negative ages are not clamped to zero. Current host, attempt, capture, component
and reference times must be usable. Component times must be ordered inside the
bounded capture. Original Puppet completion time is preserved separately.

The v1 policy defaults are:

| Policy | Boundary |
| --- | --- |
| `source_max_age_s: 3600` | Age less than 3600 seconds is fresh; equality is stale |
| `activity_window_s: 3600` | Last activity less than 3600 seconds ago is recent; equality is not recent |

The pure evaluator accepts an explicit `VerificationPolicy` with positive integer
windows and echoes it in output. Current CLI/TUI/web use the shared defaults;
there is no query syntax that silently overrides policy. Consumers must inspect
the echoed policy before adopting a result. DATA color thresholds and gwhc's
15-minute freshness policy are separate.

TC activity requires all four identity fields to agree between fresh host
configuration, latest lookup and stored TC observation. The lookup must use
`host_configuration` resolution, have valid ordered start/end times and succeed;
`successful_observation_ts` must exactly link to the stored observation. Activity
must not postdate that observation, which must not postdate lookup completion.
Failed/latest/missing or historical-identity lookups, unlinked retained success,
stale source data, unknown identity or identity mismatch leave activity unknown.
They never change configuration verification. Inactivity is not proof of an idle
or stuck worker, failed tasks, capacity or recovery eligibility.

## JSON shape

| Field in `verification` | Contents |
| --- | --- |
| `schema_version` | Integer 1; consumers check this and outer `verification_schema_version` |
| `evaluated_at`, `policy` | One evaluation clock and explicit policy |
| `configuration` | State, failing/unknown requirement names (including incomplete `snapshot`), global reasons and `supported_platform` |
| `components` | `puppet`, `revision`, `override`, `vault`, `tc_lookup`, `activity`, `workload` |
| `freshness` | Separate host, capture, file/metadata, reference, TC lookup/observation/activity clocks |
| `collection_attempt`, `snapshot` | Original collection and consistency provenance |
| `operation_authority` | Always `not_granted` |

Each component has `state`, `observed_state`, machine-readable `reasons`,
`observation_ts`, `freshness`, `provenance`, and original comparison `values`.
`state` is qualified for current use; `observed_state` is an unqualified
comparison using the displayed recorded inputs, which may be historical. It is
not a previous stored verdict or a live claim. File comparisons do not mix a
retained run with a different attempt's files. Provenance exposes attempt IDs,
run limitations, paths, expected-reference selection and TC linkage identities.
Freshness objects contain state, original observation time, nullable
`age_seconds`, `limit_s`, and an optional timestamp reason.

The JSON object is additive and leaves existing raw audit fields untouched.
JSON follow mode recomputes using recorded references and emits evidence,
comparison or freshness/activity transitions; ticking evaluation times/ages
alone do not cause updates. Web rows additionally expose the same full object;
the CONFIG popover shows components, reasons, timestamps and ages.

## Queries and presentation

| Field | Values |
| --- | --- |
| `config` (CONFIG) | `verified`, `not_verified`, `unknown` |
| `activity` (ACTIVITY) | `recent`, `not_recent`, `unknown` |
| `verify_age` (VERIFY_AGE) | Oldest required configuration-source age; missing if any required clock is unknown |
| `verify_why` (VERIFY_WHY) | Global/component failure and unknown reason codes |
| `puppet_result` | `succeeded`, `failed`, `unknown` |
| `revision_match`, `override_match`, `vault_match` | `match`, `mismatch`, `unknown` |
| `tc_lookup` | `ok`, `missing_worker`, `failed`, `unknown` |
| `host_fresh`, `reference_fresh`, `tc_fresh` | `fresh`, `stale`, `unknown` |
| `activity_reasons`, `workload` | Independent activity reasons and currently unknown workload |

Examples: `config=not_verified`, `config=not_verified|unknown sort:host`,
`config=verified activity=not_recent`, `verify_why~v1_null_is_ambiguous`,
`reference_fresh=stale sort:verify_age:desc,host`.
These are review selections, not authorized action targets. Unknown is explicit
and matchable; `config=not_verified` excludes unknown. Text snapshots put the group after HEALTHY. The interactive TUI
supports these query fields. Full evidence is available in JSON and the web view. The oldest age can describe retained evidence and never
overrides an unknown/failed current collection status.

## Compatibility and migration

PP_MATCH and HEALTHY retain their existing comparisons, filters, missing-value
behavior and Windows logic. They remain labeled compatibility signals; no saved
filter changes meaning. `unhealthy-stale.yaml` intentionally keeps its old
`healthy=n` query, excluding unknown. New `configuration-review.yaml` and
`configuration-verified.yaml` filters provide opt-in configuration selections.
Old observations without collection evidence stay unknown in the new verifier,
even if a legacy row displays HEALTHY=Y. Refresh/gather through Fleetroll to
obtain actual evidence; do not translate flat nulls into absent components.

Use CONFIG for configuration questions, ACTIVITY for the separate linked TC
signal, and retain workload/operation policy with their owners. Do not substitute
these fields into automatic recovery or rollout logic without an explicit
consumer migration. [Tool integrations](tool-integrations.md) records the
reviewed inventory and post-Puppet summary boundary.
