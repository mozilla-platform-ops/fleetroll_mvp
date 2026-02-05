# Background: Puppet Lifecycle

Because puppet changes can affect the system under test, we only run puppet when tests aren't running (either via Taskcluster quarantine or when the generic-worker process isn't running).

For the Linux workers, we do puppet runs at boot and then start the Taskcluster worker (generic-worker).

TODO: move this section lower once fully implemented/thought out.

## Linux hosts

The taskcluster worker (generic-worker) is only run if puppet passes.
- Good: Guarantees hosts are converged if the taskcluster client is working.
- Bad: Puppet failures can break every worker. Recovery involves updating the client to a better commit and running run-puppet.sh manually (or rebooting).

### puppet

- systemd runs `run-puppet.sh` script at boot
	- `run-puppet.sh` writes `/tmp/puppet_run_done` on success

### generic-worker

- `generic-worker launch script` (real name TBD) is launched by one of:
  - Ubuntu 18.04: Gnome autostarts gnome-terminal, then a Gnome Terminal autostart file
  - Ubuntu 24.04: systemd unit
- `generic-worker launch script` waits for /tmp/puppet_run_done to run, then launches g-w


## Mac hosts

generic-worker will start on Mac even if the puppet run is unsuccesful.
- Good: Bad puppet won't take out the fleet.
- Bad: No or less of a guarantee that the host is in the desired state.

### puppet

- TODO: verify mac is running at boot... not sure if it is

### generic-worker

- /Library/LaunchDaemons/org.mozilla.worker-runner.plist starts g-w if /var/tmp/semaphore/run-buildbot exists
