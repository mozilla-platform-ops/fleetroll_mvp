# Rollout Health Verification

## Problem

Users need to verify that a rollout (setting or unsetting an override) has taken effect. The verification chain is:

```
Override Set/Unset → Puppet Ran (after change) → Puppet Succeeded → TC_LAST Updated (agent healthy)
```

## Current Gaps

- No Puppet last run timestamp
- No Puppet success/failure status
- No way to correlate override change time with puppet run time with TC activity

## Solution

### 1. Add Puppet Run Data to SSH Audit

Collect from Puppet state files on the host:

```bash
# Puppet state file locations (check in order):
/opt/puppetlabs/puppet/cache/state/last_run_report.yaml   # Puppet 7+ (preferred)
/opt/puppetlabs/puppet/cache/state/last_run_summary.yaml  # Puppet 4-6
/var/lib/puppet/state/last_run_summary.yaml               # Legacy
```

Extract from YAML:
- **Report file (Puppet 7+)**: `time` (ISO timestamp) and `status` (failed/changed/unchanged)
- **Summary file (older)**: `time.last_run` (Unix epoch) and `events.failure` (count)

### 2. Extend Audit Data Model

Add to `observed` in audit records:

```python
"puppet_last_run_epoch": int,   # Unix timestamp of last puppet run
"puppet_success": bool,         # True if events.failure == 0
```

### 3. Add Monitor Columns

#### PP_LAST - Time Since Last Puppet Run

Shows **time since last puppet run** (regardless of success/failure).

| Value | Meaning |
|-------|---------|
| `2m` | Last run was 2 minutes ago |
| `6h` | Last run was 6 hours ago |
| `--` | No puppet run data available |
| `FAIL` | Last run failed (time still shown, e.g. `2m FAIL`) |

Color coding:
- Green: < 1 hour and succeeded
- Yellow: < 6 hours and succeeded
- Red: >= 6 hours OR failed

#### APPLIED - Override Applied Successfully

Shows whether the current override has been applied by puppet.

| Value | Meaning |
|-------|---------|
| `Y` | Override set AND puppet ran after AND succeeded |
| `N` | Override set but puppet hasn't run after, or failed |
| `-` | No override present |

Logic:
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
| `Y` | Applied AND TC_LAST < 1 hour |
| `N` | Not applied OR TC_LAST stale |
| `-` | No override present |

Logic:
```
HEALTHY = APPLIED AND tc_last_active_age < 1 hour
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

Key timestamps used:
- `override_mtime_epoch` - when override file was last modified
- `puppet_last_run_epoch` - when puppet last ran
- `tc_last_date_active` - when TC worker was last active

### 5. Implementation Tasks

1. **Update SSH audit script** (`fleetroll/ssh.py`)
   - Add puppet state file detection and parsing
   - Extract last_run timestamp and success status

2. **Extend audit data model** (`fleetroll/audit.py`)
   - Add puppet fields to observed schema

3. **Update monitor display** (`fleetroll/commands/monitor.py`)
   - Add PP_LAST column (time since last puppet run + FAIL indicator)
   - Add APPLIED column (override applied by puppet)
   - Add HEALTHY column (applied + TC active)
   - Implement color coding for all new columns

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
- Configurable thresholds for "healthy" TC_LAST age (currently hardcoded to 1 hour)
- Automated alerts when rollout is stuck (APPLIED=N for extended period)
