# Puppet execution in Ronin

Puppet scheduling depends on the worker role. Do not assume that every host
converges on a periodic timer or that Taskcluster activity proves Puppet success.
These are Ronin launch arrangements; inspect current configuration when diagnosing
an individual host.

| Workers | Trigger | Worker coordination |
| --- | --- | --- |
| Linux test | `puppet::atboot`: systemd once per boot, after networking | `run-start-worker.sh` waits for `/tmp/puppet_run_done`; startup depends on successful Puppet |
| macOS test/build | `macos_run_puppet` + `worker_runner`: before each worker cycle | `/var/tmp/semaphore/run-buildbot` triggers worker-runner; generic-worker can start despite Puppet failure |
| macOS signing | `puppet::periodic`: every 900 seconds | Independent of scriptworker; no generic-worker wrapper |

## Linux test workers

`run-puppet.service` is a oneshot service wanted by `default.target`, ordered
after `network-online.target`, with `KillMode=mixed`. It executes
`/usr/local/bin/run-puppet.sh`, which updates the Puppet checkout, applies the
role, writes metadata, and creates `/tmp/puppet_run_done` for worker startup.
A failed run can prevent the worker starting; inspect the failure before retrying.

Roles include `gecko_t_linux_2204_talos`, `gecko_t_linux_2404_talos`,
`gecko_t_linux_2404_talos_wayland`, `gecko_t_linux_2404_netperf`,
`gecko_t_linux_netperf`, and `gecko_t_linux_talos`.

Key paths are `/lib/systemd/system/run-puppet.service`,
`/usr/local/bin/run-puppet.sh`, and `/etc/puppet/last_run_metadata.json`.

## macOS test and build workers

`/Library/LaunchDaemons/org.mozilla.worker-runner.plist` uses `KeepAlive/PathState`
to watch `/var/tmp/semaphore/run-buildbot`; `RunAtLoad` is false. The legacy
`run-buildbot` name remains from Buildbot.

The worker-user wrapper `/usr/local/bin/worker-runner.sh` cleans task directories
and launch-services state, runs `/usr/local/bin/run-puppet.sh` with sudo when
available, then starts `/usr/local/bin/start-worker`. After worker exit it
triggers a reboot. Puppet failure does not guarantee worker startup is blocked,
so check reported application state separately.

Roles include test families `gecko_t_osx_1015_r8`, `gecko_t_osx_1400_r8`,
`gecko_t_osx_1500_m4` (with staging/IPv6 variants), and `gecko_t_osx_1500_m_vms`;
build families include `gecko_{1,3}_b_osx_{1015,arm64}`,
`nss_{1,3}_b_osx_1015`, `applicationservices_{1,3}_b_osx_1015`,
`enterprise_{1,3}_b_osx_arm64`, and `mozillavpn_b_{1,3}_osx`.

## macOS signing workers

`/Library/LaunchDaemons/com.mozilla.periodic_puppet.plist` runs
`/usr/local/bin/periodic-puppet.sh` every 15 minutes (`StartInterval=900`,
`RunAtLoad=false`). It updates/applies `macos-signer-latest` and sends Telegraf
telemetry. A separate `/usr/local/bin/run-puppet.sh` supports manual runs.

The `mac_v3_signing` profile covers v3/v4 signing roles: v3 dep/Firefox-production/
Thunderbird-production and v4 adhoc/dep/Firefox-production/enterprise/
Thunderbird-production/VPN-production. These hosts use scriptworker.

## Operator-triggered runs

`/usr/local/bin/run-puppet.sh` is the underlying entry point. Prefer Fleetroll's
`host run-puppet` workflow for managed hosts; use its `--help` for confirmation
and execution options. Script implementations differ in metadata, telemetry, and
failure reporting. Match the writer's output path to the
[metadata collector](reference/puppet-state-tracking.md).

## Ronin source map

Paths below are relative to the ronin_puppet repository:

| Component | Source |
| --- | --- |
| Linux service | `modules/puppet/files/puppet_2404.service` |
| Linux wrapper | `modules/puppet/templates/puppet-ubuntu-run-puppet.sh.erb` |
| Linux minimal wrapper (`puppet::run_script`) | `modules/puppet/templates/puppet-ubuntu-run-puppet-barebones.sh.erb` |
| Linux worker wait | `modules/linux_generic_worker/templates/run-start-worker.sh.erb` |
| Metadata writer | `modules/puppet/files/puppet_state_functions.sh` |
| macOS Puppet template (`puppet::atboot`/`puppet::periodic`) | `modules/puppet/templates/puppet-darwin-run-puppet.sh.erb` |
| macOS periodic/atboot plists | `modules/puppet/files/com.mozilla.periodic_puppet.plist`, `org.mozilla.atboot_puppet.plist` |
| macOS worker plist | `modules/worker_runner/templates/org.mozilla.worker-runner.plist.erb` |
| macOS worker wrapper | `modules/worker_runner/templates/worker-runner.sh.erb` |
| macOS standalone Puppet | `modules/macos_run_puppet/files/run-puppet.sh` |

See [rollout verification](rollout-health-verification.md) for interpreting
collected evidence and [reference scripts](../references/README.md) for integration.
