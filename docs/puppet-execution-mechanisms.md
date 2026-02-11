# Puppet Execution Mechanisms in Ronin

This document describes how Puppet runs (automatically or manually) across different operating systems and roles in the Ronin infrastructure.

## Overview

Puppet execution varies by operating system and worker role:
- **Linux test workers**: Automatic execution at boot via systemd, with semaphore coordination
- **macOS test/build workers**: Automatic execution before each worker run via worker-runner wrapper
- **macOS signing workers**: Periodic automatic execution every 15 minutes via LaunchDaemon

---

## Linux Roles

### Linux Test Workers (Automatic - puppet::atboot)

**Roles:**
- gecko_t_linux_2204_talos
- gecko_t_linux_2404_talos
- gecko_t_linux_2404_talos_wayland
- gecko_t_linux_2404_netperf
- gecko_t_linux_netperf
- gecko_t_linux_talos

**Puppet Class:** `puppet::atboot`

**Process Flow:**

1. **Boot Time:**
   - Systemd service `run-puppet.service` is configured with `WantedBy=default.target`
   - Service runs `/usr/local/bin/run-puppet.sh` as a oneshot service
   - Waits for `network-online.target` before starting

2. **During Puppet Run:**
   - Script clones/updates ronin_puppet repository
   - Applies puppet configuration
   - Writes metadata to `/etc/puppet/last_run_metadata.json` (via puppet_state_functions.sh)
   - **Creates semaphore file:** `touch /tmp/puppet_run_done`

3. **Worker Startup:**
   - The `run-start-worker.sh` script (part of linux_generic_worker) starts
   - **Waits for semaphore:** Loops checking for `/tmp/puppet_run_done` existence
   - Once semaphore exists, proceeds to start generic-worker
   - This ensures puppet completes before performance-sensitive tasks begin

**Key Files:**
- Service: `/lib/systemd/system/run-puppet.service`
- Script: `/usr/local/bin/run-puppet.sh`
- Semaphore: `/tmp/puppet_run_done`
- Metadata: `/etc/puppet/last_run_metadata.json`

**Systemd Service Definition:**
```ini
[Unit]
Description=masterless puppet run
Wants=network-online.target
After=network-online.target

[Service]
ExecStart=/usr/local/bin/run-puppet.sh
Type=oneshot
KillMode=mixed

[Install]
WantedBy=default.target
```

---

## macOS Roles

### macOS Test/Build Workers (Automatic - worker_runner)

**Roles:**
- gecko_t_osx_1015_r8 (and _staging)
- gecko_t_osx_1400_r8 (and _staging)
- gecko_t_osx_1500_m4 (and _staging, _ipv6)
- gecko_t_osx_1500_m_vms
- gecko_1_b_osx_1015 (and _staging)
- gecko_1_b_osx_arm64
- gecko_3_b_osx_1015
- gecko_3_b_osx_arm64
- nss_1_b_osx_1015
- nss_3_b_osx_1015
- applicationservices_1_b_osx_1015
- applicationservices_3_b_osx_1015
- enterprise_1_b_osx_arm64
- enterprise_3_b_osx_arm64
- mozillavpn_b_1_osx
- mozillavpn_b_3_osx

**Puppet Classes:**
- `macos_run_puppet` - provides the run-puppet.sh script
- `worker_runner` - sets up the worker-runner wrapper

**Process Flow:**

1. **Initial Setup:**
   - LaunchDaemon `org.mozilla.worker-runner.plist` is installed
   - Configured to start when semaphore file exists: `/var/tmp/semaphore/run-buildbot`
   - Runs as the worker user (typically 'cltbld')

2. **Worker Startup Trigger:**
   - LaunchDaemon monitors for `/var/tmp/semaphore/run-buildbot`
   - When file exists, starts `/usr/local/bin/worker-runner.sh`

3. **During worker-runner.sh Execution:**
   - Performs cleanup (old task dirs, launch services database)
   - **Checks for puppet script:**
     ```bash
     if [ -x /usr/local/bin/run-puppet.sh ]; then
         sudo /usr/local/bin/run-puppet.sh
     else
         echo "run-puppet.sh not found, skipping Puppet run."
     fi
     ```
   - Runs puppet if script exists and is executable
   - Then starts `/usr/local/bin/start-worker` (generic-worker)
   - After worker exits, triggers system reboot

**Key Files:**
- LaunchDaemon: `/Library/LaunchDaemons/org.mozilla.worker-runner.plist`
- Wrapper: `/usr/local/bin/worker-runner.sh`
- Puppet: `/usr/local/bin/run-puppet.sh`
- Trigger: `/var/tmp/semaphore/run-buildbot`

**LaunchDaemon Key Settings:**
```xml
<key>RunAtLoad</key>
<false/>
<key>KeepAlive</key>
<dict>
    <key>PathState</key>
    <dict>
        <key>/var/tmp/semaphore/run-buildbot</key>
        <true/>
    </dict>
</dict>
```

**Note:** The semaphore file name `run-buildbot` is legacy from the Buildbot era but still in use.

---

### macOS Signing Workers (Automatic - puppet::periodic)

**Roles:**
- mac_v3_signing (base profile used by all v3/v4 signing roles)
- mac_v3_signing_dep
- mac_v3_signing_ff_prod
- mac_v3_signing_tb_prod
- mac_v4_signing_adhoc
- mac_v4_signing_dep
- mac_v4_signing_ff_prod
- mac_v4_signing_ff_ent_prod
- mac_v4_signing_tb_prod
- mac_v4_signing_vpn_prod

**Puppet Class:** `puppet::periodic`

**Process Flow:**

1. **Periodic Execution:**
   - LaunchDaemon `com.mozilla.periodic_puppet.plist` runs every 15 minutes (900 seconds)
   - Executes `/usr/local/bin/periodic-puppet.sh`
   - Does NOT run at boot (RunAtLoad: false)

2. **During Puppet Run:**
   - Script clones/updates ronin_puppet repository
   - Applies puppet configuration from `macos-signer-latest` branch
   - Sends telemetry to Telegraf

**Key Files:**
- LaunchDaemon: `/Library/LaunchDaemons/com.mozilla.periodic_puppet.plist`
- Script: `/usr/local/bin/periodic-puppet.sh`
- Also creates: `/usr/local/bin/run-puppet.sh` (for manual runs)

**LaunchDaemon Key Settings:**
```xml
<key>Label</key>
<string>com.mozilla.periodic_puppet</string>
<key>StartInterval</key>
<integer>900</integer>
<key>RunAtLoad</key>
<false/>
<key>ProgramArguments</key>
<array>
    <string>/bin/bash</string>
    <string>/usr/local/bin/periodic-puppet.sh</string>
</array>
```

**Note:** These workers are signing-only and do not use generic-worker, so they don't need the worker-runner wrapper pattern.

---

## Manual Puppet Execution

All systems have `/usr/local/bin/run-puppet.sh` available for manual puppet runs:

```bash
# Linux
sudo /usr/local/bin/run-puppet.sh

# macOS
sudo /usr/local/bin/run-puppet.sh
```

The script will:
1. Check network connectivity
2. Clone or update the ronin_puppet repository
3. Apply the puppet configuration for the detected role
4. Write run metadata (Linux only: puppet_state_functions.sh)
5. Send telemetry to Telegraf
6. Email reports on failures

---

## Puppet Script Sources

Different classes provide different implementations of run-puppet.sh:

### Linux
- **puppet::atboot**: Uses `puppet/templates/puppet-ubuntu-run-puppet.sh.erb`
- **puppet::run_script**: Uses `puppet/templates/puppet-ubuntu-run-puppet-barebones.sh.erb` (minimal version)

### macOS
- **puppet::atboot**: Uses `puppet/templates/puppet-darwin-run-puppet.sh.erb` (supports boot-time execution with LaunchDaemon)
- **puppet::periodic**: Uses `puppet/templates/puppet-darwin-run-puppet.sh.erb` (same template, periodic mode)
- **macos_run_puppet**: Uses `macos_run_puppet/files/run-puppet.sh` (static file, different implementation)

---

## Summary Table

| OS | Worker Type | Puppet Class | Execution Trigger | Frequency | Semaphore/Coordination |
|---|---|---|---|---|---|
| Linux | Test workers | puppet::atboot | Systemd at boot | Once per boot | Creates /tmp/puppet_run_done; run-start-worker waits for it |
| macOS | Test/Build workers | macos_run_puppet + worker_runner | LaunchDaemon via worker-runner.sh | Before each worker run | Triggered by /var/tmp/semaphore/run-buildbot |
| macOS | Signing workers | puppet::periodic | LaunchDaemon periodic | Every 15 minutes | None (runs independently) |

---

## Related Files

- Linux systemd service: `modules/puppet/files/puppet_2404.service`
- Linux run-puppet template: `modules/puppet/templates/puppet-ubuntu-run-puppet.sh.erb`
- Linux worker wrapper: `modules/linux_generic_worker/templates/run-start-worker.sh.erb`
- Linux state functions: `modules/puppet/files/puppet_state_functions.sh`
- macOS periodic plist: `modules/puppet/files/com.mozilla.periodic_puppet.plist`
- macOS atboot plist: `modules/puppet/files/org.mozilla.atboot_puppet.plist`
- macOS worker-runner plist: `modules/worker_runner/templates/org.mozilla.worker-runner.plist.erb`
- macOS worker-runner script: `modules/worker_runner/templates/worker-runner.sh.erb`
- macOS run-puppet (standalone): `modules/macos_run_puppet/files/run-puppet.sh`

---

## Notes

1. **Legacy naming:** The `/var/tmp/semaphore/run-buildbot` file on macOS is a legacy name from the Buildbot era (pre-Taskcluster).

2. **Puppet branches:**
   - Linux and macOS test/build workers use `master` branch
   - macOS signing workers use `macos-signer-latest` branch

3. **Semaphore purpose on Linux:** The `/tmp/puppet_run_done` semaphore ensures that puppet configuration changes complete before performance-sensitive test tasks begin, avoiding I/O and CPU interference during benchmarking.

4. **macOS test workers don't use puppet::atboot:** Unlike Linux, macOS test workers don't run puppet at boot. Instead, puppet runs before each worker cycle as part of the worker-runner wrapper, ensuring fresh configuration before each task.

5. **Signing workers are different:** macOS signing workers run scriptworker (not generic-worker), so they use a simpler periodic puppet model without worker lifecycle coordination.
