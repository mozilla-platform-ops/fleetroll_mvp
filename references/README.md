# Puppet Run Reference Scripts

This directory contains production-ready reference implementations of puppet wrapper scripts with integrated state tracking for fleetroll.

## Contents

- **`puppet_state_functions.sh`** - Reusable bash function library for writing puppet state metadata
- **`run-puppet-linux.sh`** - Linux reference implementation with state tracking
- **`run-puppet-macos.sh`** - macOS reference implementation with state tracking
- **`test-state-writing.sh`** - Development test script (tests the function implementation)
- **`verify-state-file.sh`** - Production verifier (checks existing state file format)
- **`README.md`** - This file

## Overview

These scripts integrate with fleetroll's puppet state tracking feature by writing metadata to `/etc/puppet/last_run_metadata.json` after each puppet run. This provides ground-truth tracking of applied configuration instead of relying on heuristics.

See [`docs/puppet-state-tracking.md`](../docs/puppet-state-tracking.md) for complete feature documentation.

## State File Format

**Location**: `/etc/puppet/last_run_metadata.json` (same path on Linux and macOS)

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
- `override_sha` - SHA256 of override file that was applied (null if none)
- `vault_sha` - SHA256 of vault file that was used
- `override_path` - Path to override file (OS-specific)
- `role` - Puppet role that was applied
- `duration_s` - How long the puppet run took in seconds

## What's Been Added

The reference scripts are based on the examples in `examples/` with minimal changes:

### Integration Points

**1. Source the state writing function library** (added to top of script):
```bash
if [ -f "/etc/puppet/lib/puppet_state_functions.sh" ]; then
    source "/etc/puppet/lib/puppet_state_functions.sh"
else
    echo "WARNING: Could not load state writing function from /etc/puppet/lib/puppet_state_functions.sh" >&2
fi
```

**2. Call state writer after puppet run** (added in `run_puppet` function):
```bash
# After puppet completes and exit code is captured
PUPPET_RUN_DURATION=$SECONDS

if type write_puppet_state >/dev/null 2>&1; then
    write_puppet_state "$WORKING_DIR" "$ROLE" "$retval" "$PUPPET_RUN_DURATION" \
        "/etc/puppet/ronin_settings" "/root/vault.yaml"  # Linux
        # OR
        "/opt/puppet_environments/ronin_settings" "/var/root/vault.yaml"  # macOS
else
    echo "WARNING: write_puppet_state function not available" >&2
fi
```

**That's it!** All other functionality remains unchanged.

## Platform-Specific Paths

### Linux
- Override path: `/etc/puppet/ronin_settings`
- Vault path: `/root/vault.yaml`
- SHA command: `sha256sum` (auto-detected)
- State file: `/etc/puppet/last_run_metadata.json`

### macOS
- Override path: `/opt/puppet_environments/ronin_settings`
- Vault path: `/var/root/vault.yaml`
- SHA command: `shasum -a 256` (auto-detected)
- State file: `/etc/puppet/last_run_metadata.json`

## Deployment

### Prerequisites

1. Ensure `puppet_state_functions.sh` is deployed to `/etc/puppet/lib/`
2. Ensure `/etc/puppet/lib/` directory exists
3. Ensure `/etc/puppet/` directory exists (already present for ronin_settings)

### Deployment Steps

1. **Deploy both files to target hosts**:
   ```bash
   # Standard deployment locations:
   /usr/local/bin/run-puppet.sh                 # Main script (executable)
   /etc/puppet/lib/puppet_state_functions.sh    # State writer function library
   ```

2. **Create library directory if needed**:
   ```bash
   sudo mkdir -p /etc/puppet/lib
   sudo chmod 755 /etc/puppet/lib
   ```

3. **Verify the integration**:
   The run-puppet scripts are already configured to source from `/etc/puppet/lib/puppet_state_functions.sh`

3. **Test the integration**:
   ```bash
   # Run the test script
   ./test-state-writing.sh

   # Or test with actual puppet run
   sudo ./run-puppet-linux.sh  # or run-puppet-macos.sh

   # Verify state file was created
   sudo cat /etc/puppet/last_run_metadata.json
   ```

## Customization

If you need to customize for your environment:

### Change State File Location

Pass the optional 7th parameter to `write_puppet_state`:
```bash
write_puppet_state "$WORKING_DIR" "$ROLE" "$retval" "$DURATION" \
    "$OVERRIDE_PATH" "$VAULT_PATH" "/custom/path/state.json"
```

### Change Override/Vault Paths

Simply change the paths in the `write_puppet_state` call:
```bash
write_puppet_state "$WORKING_DIR" "$ROLE" "$retval" "$DURATION" \
    "/custom/override/path" "/custom/vault/path"
```

## Error Handling

**Critical**: The state writing function is designed to **never fail the puppet run**.

- All errors are logged to stderr but execution continues
- If state write fails, puppet run proceeds normally
- If state function is not available, a warning is logged but script continues
- If git info cannot be extracted, null values are used

This ensures puppet convergence is never blocked by state tracking.

## Testing

### Development Testing

Use `test-state-writing.sh` to test the function implementation during development:

```bash
# Run from git repo directory
cd /path/to/fleetroll_mvp
bash references/test-state-writing.sh
```

This test:
- Sources and tests the `puppet_state_functions.sh` function
- Creates temporary git repos and config files
- Validates function behavior in various scenarios (clean/dirty repos, success/failure)
- Tests JSON format and field values

### Production Verification

Use `verify-state-file.sh` to check the existing state file on production hosts:

```bash
# Copy verifier to host and run
scp references/verify-state-file.sh user@host:~/
ssh user@host
sudo ./verify-state-file.sh
```

This verifier:
- Checks that `/etc/puppet/last_run_metadata.json` exists
- Validates JSON format
- Verifies all required fields are present
- Shows current file content and age
- Does NOT require `puppet_state_functions.sh` to be present

### Manual Testing

After deploying, verify state tracking works:

```bash
# 1. Run puppet
sudo /usr/local/bin/run-puppet.sh

# 2. Check state file exists
sudo ls -la /etc/puppet/last_run_metadata.json

# 3. Verify JSON is valid
sudo cat /etc/puppet/last_run_metadata.json | python3 -m json.tool

# 4. Check fleetroll can read it
fleetroll host-audit hostname
fleetroll host-monitor host-list.txt
```

## Fleetroll Integration

Once deployed, fleetroll commands will automatically use the new state file:

```bash
# Audit host (reads state file via SSH)
fleetroll host-audit t-linux64-ms-238

# Monitor hosts (shows APPLIED status based on state)
fleetroll host-monitor hosts.txt
```

The monitor will show:
- **PP_SHA** - Git SHA that puppet applied (7 chars)
- **PP_LAST** - Time since last puppet run
- **APPLIED** - Whether current override was applied (SHA comparison)
- **HEALTHY** - Whether applied AND worker is active

## Backward Compatibility

The state file is **additive only**:
- Hosts without the new script will continue to work (fleetroll falls back to old YAML parsing)
- Hosts with the new script will provide enhanced tracking
- You can roll out gradually without breaking existing hosts

## Troubleshooting

### State file not created

Check:
1. Is `puppet_state_functions.sh` installed at `/etc/puppet/lib/puppet_state_functions.sh`?
2. Does `/etc/puppet/lib/` directory exist?
3. Does the puppet script have permission to write to `/etc/puppet/`?
4. Check stderr output for warnings

### Invalid JSON in state file

Check:
1. Is the state file being written atomically? (Should be temp file + rename)
2. Are multiple processes writing to the same file? (Should be atomic, but check)
3. Run test script to validate function works

### Git info is null

This is normal if:
- Puppet working directory is not a git repo
- Git commands fail (permissions, missing git binary)
- The function logs errors to stderr but continues

### Fleetroll not reading state file

Check:
1. Does fleetroll have SSH access to the host?
2. Does the SSH user have permission to read `/etc/puppet/last_run_metadata.json`?
3. Check fleetroll audit logs for errors

## Support

For issues or questions:
- See [`docs/puppet-state-tracking.md`](../docs/puppet-state-tracking.md) for feature details
- Check the test script output for validation errors
- Review stderr logs from puppet runs for state writing warnings

## Related Documentation

- [`docs/puppet-state-tracking.md`](../docs/puppet-state-tracking.md) - Complete feature documentation
- [`docs/rollout-health-verification.md`](../docs/rollout-health-verification.md) - Health verification workflow
- `examples/` - Original example scripts (before state tracking integration)
