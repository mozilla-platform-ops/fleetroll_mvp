# Rollout Health Verification

## Problem

Users need to verify that a rollout (setting or unsetting an override) has taken effect. The verification chain is:

```
Override Set/Unset → Puppet Ran (after change) → Puppet Succeeded → TC_ACT Updated (agent healthy)
```

## Current Gaps

- No Puppet last run timestamp
- No Puppet success/failure status
- No way to correlate override change time with puppet run time with TC activity

## Solution

### 1. Puppet State Metadata File

The puppet wrapper script writes ground-truth state after each run:

**Location**: `/etc/puppet/last_run_metadata.json` (both Linux and macOS)

**Format**: JSON with these key fields:
- `ts` - ISO 8601 timestamp when puppet completed
- `success` - Boolean (true if exit codes 0 or 2)
- `exit_code` - Actual puppet exit code
- `git_sha` - Full commit SHA that was applied
- `git_branch` - Branch name
- `git_repo` - Repository URL
- `override_sha` - SHA256 of override file applied (null if none)
- `vault_sha` - SHA256 of vault file used
- `role` - Puppet role applied
- `duration_s` - Run duration in seconds

See [`docs/puppet-state-tracking.md`](puppet-state-tracking.md) for complete specification.

### 2. Audit Data Model

The SSH audit script reads the state file and extracts fields into `observed`:

```python
"puppet_state_ts": str,                    # ISO timestamp from state file
"puppet_success": bool,                    # success field from state file
"puppet_git_sha": str,                     # git SHA puppet applied
"puppet_override_sha_applied": str,        # SHA256 of override puppet applied (null if none)
"puppet_vault_sha_applied": str,           # SHA256 of vault puppet used
"puppet_role": str,                        # role puppet applied
"puppet_exit_code": int,                   # puppet exit code
"puppet_duration_s": int,                  # run duration
# ... plus git_repo, git_branch, git_dirty
```

Backward compatibility: Falls back to legacy YAML parsing if JSON file not present.

### 3. Monitor Columns

#### PP_SHA - Git SHA Applied

Shows the 7-character git SHA that puppet applied (truncated from full SHA).

| Value | Meaning |
|-------|---------|
| `abc1234` | Puppet applied git commit abc1234 |
| `--` | No puppet state data available |

Color coding: Gray (informational)

#### PP_LAST - Time Since Last Puppet Run

Shows **time since last puppet run** (regardless of success/failure).

| Value | Meaning |
|-------|---------|
| `2m` | Last run was 2 minutes ago |
| `6h` | Last run was 6 hours ago |
| `--` | No puppet run data available |
| `FAIL` | Last run failed (time still shown, e.g. `2m FAIL`) |

Uses `puppet_state_ts` from state file (falls back to legacy `puppet_last_run_epoch` if unavailable).

Color coding:
- Green: < 1 hour and succeeded
- Yellow: < 6 hours and succeeded
- Red: >= 6 hours OR failed

#### APPLIED - Override Applied Successfully

Shows whether the current override has been applied by puppet using **SHA comparison** (ground truth).

| Value | Meaning |
|-------|---------|
| `Y` | Override SHA matches what puppet applied AND succeeded |
| `N` | Override present but not applied by puppet, or puppet failed |
| `-` | No override present |

Logic (primary method using state file):
```
APPLIED = puppet_override_sha_applied == current_override_sha256
          AND puppet_success
```

Fallback (legacy timestamp heuristic if state file unavailable):
```
APPLIED = override_present
          AND puppet_last_run_epoch > override_mtime_epoch
          AND puppet_success
```

Color coding:
- Green: `Y`
- Yellow: `N` (waiting for puppet)
- Gray: `-` (no override)

#### HEALTHY - Rollout Health Status

Shows overall health: override applied AND worker is active.

| Value | Meaning |
|-------|---------|
| `Y` | Applied AND TC_ACT < 1 hour |
| `N` | Not applied OR TC_ACT stale |
| `-` | No override present |

Logic:
```
HEALTHY = APPLIED AND tc_act_age < 1 hour
```

Color coding:
- Green: `Y`
- Red: `N`
- Gray: `-`

### 4. Health Assessment Summary

The verification chain for a successful rollout:

```
Override Set → Puppet Ran (after mtime) → Puppet Succeeded → TC Active
     │                  │                        │              │
     └──────────────────┴────────────────────────┘              │
                        APPLIED = Y                             │
                        └───────────────────────────────────────┘
                                    HEALTHY = Y
```

Key data used:
- `override_sha256` - SHA256 of current override file on host
- `puppet_override_sha_applied` - SHA256 of override puppet applied (from state file)
- `puppet_state_ts` - when puppet last ran (from state file)
- `tc_act_date_active` - when TC worker was last active

### 5. Implementation

The puppet state tracking feature has been implemented. See epic mvp-3kp for details.

**Components**:

1. **State writing function** (`references/puppet_state_functions.sh`)
   - Reusable bash function for writing state metadata
   - OS detection for SHA commands (Linux/macOS)
   - Atomic writes with error handling

2. **Reference puppet scripts** (`references/run-puppet-*.sh`)
   - Linux (ERB template) and macOS implementations
   - Integration with state writing function

3. **SSH audit script** (`fleetroll/ssh.py`)
   - Reads `/etc/puppet/last_run_metadata.json` via SSH
   - Parses JSON fields into PP_* key-value pairs
   - Falls back to legacy YAML if JSON unavailable

4. **Audit parser** (`fleetroll/audit.py`)
   - Processes PP_* fields into observed dict
   - Backward compatibility with old field names

5. **Monitor display** (`fleetroll/commands/monitor/`)
   - PP_SHA column (7-char git SHA)
   - PP_LAST column (time since last run)
   - APPLIED logic (SHA comparison with timestamp fallback)
   - HEALTHY column (applied + TC active)

**Deployment**: See [`references/README.md`](../references/README.md) for deployment guide.

### 6. Verification Workflow

For a user checking rollout health:

1. **Set/unset override** - timestamp recorded in audit log
2. **Wait for puppet interval** (typically 30 min)
3. **Run audit** to refresh host state
4. **Check monitor columns**:
   - `PP_LAST` - puppet ran recently (green = good)
   - `APPLIED` - shows `Y` (puppet applied the override)
   - `HEALTHY` - shows `Y` (override applied AND worker active)

The `HEALTHY` column is the single indicator for rollout success.

## Future Enhancements

- Rollout progress summary showing % of hosts where HEALTHY=Y
- Configurable thresholds for "healthy" TC_ACT age (currently hardcoded to 1 hour)
- Automated alerts when rollout is stuck (APPLIED=N for extended period)
