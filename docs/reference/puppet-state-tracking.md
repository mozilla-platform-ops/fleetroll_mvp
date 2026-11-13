# Puppet run metadata

The Puppet wrapper writes `last_run_metadata.json` after a run so Fleetroll can
compare observed files and expected revisions with the state reported by Puppet.
Collection and monitoring are implemented (epic `mvp-3kp`); hosts need a wrapper
that writes the file.

| Platform | Collector reads | Override | Vault |
| --- | --- | --- | --- |
| Linux | `/etc/puppet/last_run_metadata.json` | `/etc/puppet/ronin_settings` | `/root/vault.yaml` |
| macOS | `/opt/puppet_environments/last_run_metadata.json` | `/opt/puppet_environments/ronin_settings` | `/var/root/vault.yaml` |

The reference writer defaults to the Linux metadata path on both platforms.
Pass its optional output path for the macOS collector location. See
[reference scripts and deployment](../../references/README.md); verify the writer
and collector paths agree when integrating a wrapper.

## File contract

```json
{
  "schema_version": 1,
  "ts": "2026-11-04T12:00:00Z",
  "success": true,
  "exit_code": 2,
  "git_repo": "https://github.com/mozilla-platform-ops/ronin_puppet.git",
  "git_branch": "master",
  "git_sha": "example_revision",
  "git_dirty": false,
  "override_sha": null,
  "vault_sha": "example_vault_checksum",
  "override_path": "/etc/puppet/ronin_settings",
  "role": "gecko_t_linux_2404_talos",
  "duration_s": 45
}
```

Revision/checksum strings above are placeholders. Fields describe the run:

| Fields | Meaning |
| --- | --- |
| `schema_version` | Metadata format version, currently 1 |
| `ts`, `duration_s` | Completion timestamp in ISO 8601 and elapsed seconds |
| `success`, `exit_code` | Puppet exit 0 (unchanged) or 2 (changes applied) is successful; other codes indicate failure |
| `git_repo`, `git_branch`, `git_sha`, `git_dirty` | Repository, branch, applied commit, and working-tree dirtiness |
| `override_sha`, `override_path` | Override checksum and path; checksum is null when absent |
| `vault_sha`, `role` | Vault checksum used and Puppet role |

The reference [writer](../../references/puppet_state_functions.sh) computes hashes
with `sha256sum` or `shasum -a 256` and writes atomically using a temporary file
and rename. Metadata failures must not fail the Puppet run; unavailable values
use JSON null. The file is intended to be readable by the auditing SSH user
(reference permissions: 0644).

## Collection and interpretation

The SSH collector reads and base64-encodes the JSON; `fleetroll/audit.py` parses
it locally. Fields become `observed.puppet_state_ts`, `puppet_success`,
`puppet_git_repo`, `puppet_git_branch`, `puppet_git_sha`, `puppet_git_dirty`,
`puppet_override_sha_applied`, `puppet_vault_sha_applied`, `puppet_role`,
`puppet_exit_code`, and `puppet_duration_s`. The timestamp also populates
`puppet_last_run_epoch` when parseable.

When the JSON file is absent, the collector tries legacy Puppet YAML for run
time and success. Malformed JSON does not supply applied-state proof. Managed
macOS hosts typically lack those YAML files, so they need the JSON writer.

A file write is not proof that Puppet applied it. Compare deployed and reported
checksums and require a successful run. Expected branch revision and Taskcluster
activity are separate evidence; the monitor and rollout report apply different
checks. See [rollout verification](../rollout-health-verification.md),
[monitor JSON](monitor-json.md), and [rollout report](../rollout-report.md).

## Operator-triggered run results

`fleetroll host run-puppet HOST --confirm` captures metadata immediately before
and after invoking the remote wrapper. The Linux wrapper converts both Puppet
exit 0 and exit 2 into wrapper exit 0, so wrapper success alone cannot distinguish
"no changes" from "changes applied". Fleetroll reads the detailed result from
new metadata rather than the wrapper's exit status or the local observation DB.
`--no-audit` skips the subsequent gather, but does not skip this result capture.

The metadata must change, use schema v1, have consistent typed `success` and
`exit_code` fields, and include a timezone-aware completion timestamp and a finite,
nonnegative duration. Completion must fall within the remote invocation window;
completion minus duration must not predate its start, allowing one second for
whole-second producer timing. Missing, unreadable, unchanged, malformed,
unsupported, or out-of-window metadata leaves the Puppet outcome unknown.

JSON results and audit records distinguish these `observed` fields:

| Field | Meaning |
| --- | --- |
| `wrapper_exit` | Wrapper exit code, independent of Puppet's code; null if unavailable |
| `puppet_exit` | Validated metadata exit code; null without usable evidence |
| `puppet_outcome` | `unchanged` (0), `changed` (2), `failed` (other codes), or `unknown` |
| `changes_applied` | False for codes 0/4, true for 2/6 (including partial failure); null otherwise |
| `outcome_reason` | Why evidence is unknown; null for a known Puppet result |
| `outcome_source` | `run_window_metadata_v1` for usable metadata; null otherwise |
| `puppet_state_ts` | Original metadata completion timestamp; null without usable evidence |
| `run_started_epoch`, `run_finished_epoch` | Remote-clock bounds around wrapper execution; null if unavailable |
| `duration_s` | Local elapsed SSH execution time, separate from Puppet metadata duration |

Top-level `ssh_rc` remains the outer SSH/remote collection status. `ok=true`
requires successful SSH collection, wrapper exit 0, and known Puppet success.
A wrapper failure can coexist with metadata reporting changes or an unchanged
apply: later wrapper steps may fail. A wrapper success can coexist with failed
Puppet metadata; it does not override that failure. Unknown outcomes produce
`ok=false` and a nonzero command exit. Batch summaries count unknown outcomes
separately from failed executions with known Puppet outcomes.

Compatibility correction: `puppet_exit` previously contained the wrapper code,
and `changes_applied=false` could mean there was no evidence. Consumers must now
handle null and use `puppet_outcome`, `wrapper_exit`, and top-level `ok` separately.
Legacy `EXIT=N` output alone is insufficient evidence and yields unknown.

Schema v1 has no invocation ID. These checks bound the evidence to the run window
but cannot identify a competing Puppet run wholly contained within that window.
Avoid concurrent Puppet invocations on a host. This evidence is for operator
reporting, not exclusive execution proof or authorization for automatic recovery.
The command requests `FLEETROLL_SINGLE_RUN=1`; whether a remote wrapper honors
that request is a separate capability and is not established by metadata capture.
