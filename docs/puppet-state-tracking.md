# Puppet State Tracking Feature

**Epic**: mvp-3kp
**Status**: In Planning
**Priority**: P1

## Overview

This feature adds ground-truth puppet state tracking by having the puppet wrapper script write a JSON metadata file after each run. This eliminates the need for complex heuristics to infer whether puppet has applied configuration changes.

## Problem Statement

Currently, the host-monitor APPLIED and HEALTHY columns try to infer whether puppet has applied the current state by:
1. Comparing puppet's last run time against override file mtime
2. Tracking override removal times via unset records or audit transitions

This approach is fragile and requires complex heuristics. We can't reliably determine:
- When an override was removed (if done before auditing started or manually)
- Whether puppet actually applied the current configuration
- What git SHA the host was converged at

## Solution

Have the puppet wrapper script write out a state file with the actual applied state. This gives us ground truth instead of inference.

### Puppet State Metadata File

**Location**: `/etc/puppet/last_run_metadata.json`
- Same path for both Linux and macOS
- Uses existing `/etc/puppet/` directory (already present for ronin_settings)
- Generic name (no "fleetroll" reference) - any tool can read it
- World-readable (0644) for SSH access

**Format**: JSON with schema versioning

```json
{
  "schema_version": 1,
  "ts": "2026-01-29T10:30:45Z",
  "success": true,
  "exit_code": 2,
  "git_repo": "https://github.com/mozilla-platform-ops/ronin_puppet.git",
  "git_branch": "master",
  "git_sha": "abc123def456789...",
  "override_sha": "sha256_of_override_file_or_null",
  "vault_sha": "sha256_of_vault_file",
  "override_path": "/etc/puppet/ronin_settings",
  "role": "gecko-t-linux-talos",
  "duration_s": 45
}
```

**Field Descriptions**:
- `schema_version` - Version number for future compatibility (1 for initial version)
- `ts` - ISO 8601 timestamp when puppet completed
- `success` - Boolean indicating puppet run success (exit codes 0 or 2)
- `exit_code` - Actual puppet exit code (0=no changes, 2=changes applied, other=failure)
- `git_repo` - Repository URL that was applied
- `git_branch` - Branch name that was checked out
- `git_sha` - Full commit SHA that was applied
- `override_sha` - SHA256 of override file that was applied (null if none present)
- `vault_sha` - SHA256 of vault file that was used
- `override_path` - Path to override file (OS-specific)
- `role` - Puppet role that was applied
- `duration_s` - How long the puppet run took in seconds

## Benefits

### 1. Simplified APPLIED Logic

**Before** (heuristic-based):
```
APPLIED = override_present
          AND puppet_last_run_epoch > override_mtime_epoch
          AND puppet_success
```

**After** (ground truth):
```
APPLIED = puppet_override_sha_applied == current_override_sha
          AND puppet_state_success
```

### 2. New Visibility

- **PP_SHA column**: Shows what git SHA puppet actually applied
- **Ground truth**: No more guessing about removal times or boot times
- **Audit trail**: Exact record of what was applied when

### 3. Future Capabilities

- Track rollout progress by git SHA
- Detect configuration drift
- Historical state tracking (future: JSONL format for history)

## Implementation Tasks

### Task Breakdown

All tasks are children of epic mvp-3kp with P1 priority:

1. **mvp-uvz**: Create JSON state writing function
   - Bash function in `examples/write_puppet_state.sh`
   - OS detection for SHA commands (sha256sum vs shasum)
   - Git info extraction from working directory
   - Atomic writes (temp file + rename)
   - Error handling (never fail puppet run)

2. **mvp-14e**: Create reference run-puppet.sh scripts
   - Clean implementations in `reference/` directory
   - Both Linux and macOS versions
   - Integrate state writing function
   - Document integration points

3. **mvp-2us**: Update collector to read new state file
   - Modify `fleetroll/ssh.py` audit_script_body()
   - Bash-based JSON parsing (grep/sed)
   - Output new PP_* fields
   - Backward compatibility with YAML files

4. **mvp-13x**: Update audit parser for new fields
   - Modify `fleetroll/audit.py` process_audit_result()
   - Parse new PP_* fields into observed dict
   - Prefer new fields, fall back to old

5. **mvp-ahw**: Update monitor to use new ground truth
   - Modify `fleetroll/commands/monitor/` data.py and display.py
   - Simplify APPLIED logic (SHA comparison)
   - Add PP_SHA column (7 char truncated)
   - Update PP_LAST to use new timestamp

6. **mvp-2xt**: Add tests for new functionality
   - Unit tests for state writing
   - Unit tests for JSON parsing
   - Integration tests end-to-end
   - Backward compatibility tests

7. **mvp-36e**: Update documentation
   - README puppet state section
   - Update rollout-health-verification.md
   - Document in epic

### Dependency Chain

```
mvp-uvz (foundation)
├── mvp-14e (reference scripts)
├── mvp-2us (collector)
│   └── mvp-13x (parser)
│       └── mvp-ahw (monitor)
└── mvp-36e (docs - depends on all)

mvp-2xt (tests - can run independently)
```

## Technical Details

### State Writing Function

**Key Challenges**:
- **SHA calculation**: Handle Linux (`sha256sum`) vs macOS (`shasum -a 256`)
- **Git info extraction**:
  - `git config --get remote.origin.url` - repo URL
  - `git rev-parse --abbrev-ref HEAD` - branch name
  - `git rev-parse HEAD` - commit SHA
- **JSON escaping**: Properly escape quotes, backslashes
- **ISO timestamps**: `date -u +%Y-%m-%dT%H:%M:%SZ`
- **Null handling**: Use JSON `null` not empty string

**Error Handling**:
- State write failure must not fail puppet run
- Log errors but continue
- If git commands fail, use null values
- If SHA calculation fails, use null values

### Collector Update

**JSON Parsing in Bash** (MVP approach):
```bash
if sudo -n test -e "/etc/puppet/last_run_metadata.json" 2>/dev/null; then
  state_json=$(sudo -n cat "/etc/puppet/last_run_metadata.json" 2>/dev/null || true)
  # Simple regex extraction (works for well-formed JSON)
  pp_sha=$(printf '%s' "$state_json" | grep -o '"git_sha":"[^"]*"' | cut -d'"' -f4)
  # ... extract other fields
  # If extraction fails, fall back to old YAML parsing
fi
```

**Note**: Bash JSON parsing is fragile but acceptable for MVP with well-formed JSON. Future work should migrate collector to Python for robust parsing.

**Backward Compatibility**:
- Check for new JSON file first
- Fall back to existing YAML parsing if JSON missing
- Allows gradual rollout of new run-puppet.sh scripts

### Monitor Updates

**New Columns**:

| Column | Description | Implementation |
|--------|-------------|----------------|
| PP_SHA | Git SHA puppet applied (7 chars) | Show `puppet_git_sha[:7]`, gray color |
| PP_LAST | Time since last puppet run | Use `puppet_state_ts` with fallback to old field |

**Updated Logic**:

| Column | Old Logic | New Logic |
|--------|-----------|-----------|
| APPLIED | Timestamp comparison heuristic | Direct SHA comparison: `puppet_override_sha_applied == override_sha256` |
| HEALTHY | APPLIED + TC active | Same, but simpler APPLIED |

**Removed from MVP**:
- OVR_MATCH column (rare use case - manual override changes uncommon)

## Verification

### Testing Checklist

1. **State file creation**:
   - Run modified run-puppet.sh on test host
   - Verify `/etc/puppet/last_run_metadata.json` created
   - Verify JSON is valid and contains all fields
   - Test on both Linux and macOS

2. **Collector integration**:
   - Run `fleetroll host-audit` on test host
   - Verify audit log contains new PP_* fields
   - Verify values match state file

3. **Monitor display**:
   - Run `fleetroll host-monitor`
   - Verify APPLIED shows correct status
   - Verify PP_SHA column appears with git SHA
   - Verify PP_LAST shows correct time

4. **Backward compatibility**:
   - Test with hosts that don't have JSON file
   - Verify fallback to old YAML parsing works
   - Verify monitor display works with old data

5. **Error handling**:
   - Test with malformed JSON
   - Test with missing git repo
   - Test with puppet failure
   - Verify state file write never fails puppet

## Deployment Strategy

### Phase 1: Development (This Epic)
- Create reference implementations in this repo
- Update fleetroll to read new metadata file
- Maintain backward compatibility

### Phase 2: Deployment (Future)
- Deploy new run-puppet.sh to test hosts
- Monitor for issues
- Gradual rollout to production

### Phase 3: Cleanup (Future)
- Remove old YAML parsing code
- Remove timestamp heuristics
- Simplify monitor logic

## Platform Support

### Linux
- Override path: `/etc/puppet/ronin_settings`
- Vault path: `/root/vault.yaml`
- SHA command: `sha256sum`
- State file: `/etc/puppet/last_run_metadata.json`

### macOS
- Override path: `/opt/puppet_environments/ronin_settings`
- Vault path: `/var/root/vault.yaml`
- SHA command: `shasum -a 256`
- State file: `/etc/puppet/last_run_metadata.json` (same)

## Future Enhancements

### Short Term
- Migrate collector from bash to Python for robust JSON parsing
- Add PP_SHA color coding based on expected rollout SHA
- Add rollout progress tracking by git SHA

### Long Term
- History tracking (JSONL format for multiple runs)
- Puppet run metrics and alerting
- Configuration drift detection
- Automated rollback on puppet failures

## Related Documentation

- `docs/rollout-health-verification.md` - Current health verification approach (will be updated)
- `examples/run-puppet-linux.sh` - Current Linux puppet wrapper
- `examples/run-puppet-macos.sh` - Current macOS puppet wrapper
- Plan file: `~/.claude/plans/composed-percolating-walrus.md`

## References

- Epic: mvp-3kp
- Child tasks: mvp-uvz, mvp-14e, mvp-2us, mvp-13x, mvp-ahw, mvp-2xt, mvp-36e
- Critical files:
  - `fleetroll/ssh.py` (lines 177-229)
  - `fleetroll/audit.py` (lines 169-181)
  - `fleetroll/commands/monitor/data.py`
  - `fleetroll/commands/monitor/display.py`
