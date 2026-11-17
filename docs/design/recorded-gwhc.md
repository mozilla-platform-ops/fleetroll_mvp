# Recorded gwhc diagnostics

Implements mvp-z4lh, mvp-qg2 and mvp-gpke. Collection is independent of
configuration verification, Taskcluster activity and recovery policy.

## Producer and transport

Verified the upstream `master` source at revision
`496e963b2f266a1e5b9689f1854d4457d724a1e1` on 2026-11-16:
[generic-worker-health-check](https://github.com/mozilla-platform-ops/ronin_puppet/blob/496e963b2f266a1e5b9689f1854d4457d724a1e1/modules/linux_generic_worker/files/generic-worker-health-check).
The installed executable is `gwhc`. Upstream integer schema v1 supplies
`generic_worker_process.data` with live PIDs, verified process records, zombie
PIDs and optional unverified PIDs. `disk_free.data` maps paths to available
`free_gb`, `free_pct` and `total_gb`; its GB values are actually GiB (2^30 bytes),
rounded to one decimal. The producer inspects `/`, `/var`, `/home`, skipping
nonexistent paths, zero-sized filesystems and devices already represented by an
earlier path. Consumers must retain the paths actually supplied.

The host audit runs optional `sudo -n gwhc --json` before the override-content
sentinel. GNU timeout bounds execution to 20 seconds, followed by a two-second
kill deadline. Without timeout the collector reports unavailable. Exit 0 and
exit 1 are preserved independently of SSH/core-audit success. Base64 framing
protects the existing line protocol; at most 256 KiB of report output is
accepted. Larger reports are explicitly rejected. No extra SSH connection or
database migration is needed: observations already persist JSON objects.

Shared validation lives in `fleetroll/gwhc.py`; the live diagnosis command keeps
its existing envelope and behavior. Recorded envelopes add source, bounded
stdout, `gwhc_worker` and `gwhc_disks`. Legacy reports can complete general
diagnosis, but normalized worker and disk metrics require supported v1 evidence.
Invalid optional component data remains unknown without discarding other valid
checks. A failed/unsupported report cannot supply normalized metrics.

Optional diagnostic failure never changes core host-audit success, retry policy
or command exit status. It is visible in the persisted envelope, monitor JSON
and unknown row values. Failed SSH cannot validate partial diagnostic output.
Older observations have no diagnostic evidence; they are never upgraded by
inference or by the local record's newer timestamp.

## Presentation policy

`gwhc_worker` uses verified process existence: running when a live process is
present; stopped when all identified processes are absent, zombies or exited;
unknown when process evidence is missing, conflicting or unverified with no live
process. A stopped worker may be provisioning. Running does not imply a healthy
worker, available capacity, job progress or idleness. Worker-runner checks and TC
activity cannot supply this result.

`gwhc_disk_alert` is alert when any valid path reports less than 5 GiB available
or less than 10% free, matching upstream thresholds. Boundaries 5 GiB and 10%
are not alerts. Each metric is evaluated as reported, including producer
rounding. Percentages are used only when supplied, never inferred from free
space alone. An absolute-only non-alert evaluates only the available threshold;
it makes no percentage claim. Confirmed low space remains an alert even if
another path is unreadable or the cleaner is running. Otherwise incomplete
path evidence is unknown. A missing/unreported path is not evidence of coverage.

`GW_RUN`, `DISK` and `GWHC_AGE` are shared TUI/web row values. Disk cells retain
path scope, units, percentages where supplied, and ALERT/UNKNOWN/STALE prefixes.
Evidence is stale at 900 seconds, based on the oldest of report time, remote
check-start time and host-record time. Missing/invalid timestamps or clock times
more than 60 seconds in the future make freshness unknown. After a failed host
gather, TUI/web fallback values are explicitly stale even within 15 minutes.
No newer successful optional-collection failure falls back to older metrics.

Monitor JSON preserves `observed.gwhc` and adds version 1 `gwhc_evidence` with
qualified statuses, source timestamps, collection status/reason, freshness,
underlying worker evidence and all normalized disks. Missing/failed collections
are unknown. The raw retained statuses are historical facts; callers use the
qualified statuses for freshness-sensitive presentation. This adds an independent
envelope without changing worker-evidence schema v3 or existing health behavior.

No automatic cleanup, reboot, health gate or frozen-worker inference is added.
