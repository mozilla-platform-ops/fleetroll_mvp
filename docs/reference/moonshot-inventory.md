# Moonshot inventory source and baseline

## Source and scope

The maintainer identified the Google Sheet **Moonshot Master Inventory** as the
source of `configs/host-lists/linux/all_moonshots.list` on 2026-11-13. The local
file `Moonshot Master Inventory.xlsx` supplied that day is an export of the sheet,
not a live connection or a verified Google revision. A reproducible export/import
procedure and source revision are not recorded in this repository.

The intended subject is Moonshot hardware across Windows-to-Linux and Ubuntu
18.04-to-24.04 imaging. Observed OS, current hostname, reachability, and last
Taskcluster registration can change while the hardware remains in the inventory.
The `linux/` directory name does not establish that each listed node currently
runs Linux. Offline nodes remain accounted for; unavailable evidence cannot
establish retirement, successful imaging, or current assignment.

The maintained source, local baseline, and generated list have separate roles:

```text
Google Sheet → exported inventory → all_moonshots.list → generated linux/all.list
```

`tools/generate_all_host_lists.py` implements only the final step. It reads every
host in `all_moonshots.list`, deduplicates/sorts it, and writes `linux/all.list`.
It does not read the workbook, discover missing rows, filter by OS/lifecycle, or
resolve hostname changes. No workbook-to-baseline importer was found in the
repository during this review. The baseline has no source revision header.

## Export reconciliation on 2026-11-13

This is a comparison of the supplied export's **MDC_1** sheet with the checked-in
baseline, not live host verification or a claim about the current Google Sheet.
Other workbook tabs include other sites and historical/audit material and were
not combined into this population.

| Check | Result |
| --- | --- |
| Inventory hostname rows / unique hostnames | 315 / 315 |
| Distinct chassis / cartridge / node-index combinations | 315, all components populated, no duplicates |
| Chassis distribution | Seven chassis, 45 rows each |
| Inventory hostname prefixes | 250 `t-linux64-ms`, 65 `t-w1064-ms` |
| Baseline entries / unique hostnames | 280 / 280 |
| Baseline numeric suffixes | 001–280, no internal gaps |
| Exact hostnames shared by sheet and baseline | 250 |
| Same-numbered Windows names versus baseline Linux names | 30: suffixes 241–270 |
| Sheet suffixes absent from the baseline | 35: 281–300 and 616–630 |

The 30 same-numbered pairs use different prefixes and DNS domains:
`t-linux64-ms-NNN.test.releng.mdc1.mozilla.com` in the baseline versus
`t-w1064-ms-NNN.wintest.releng.mdc1.mozilla.com` in the export. The maintainer
subsequently confirmed that the numeric `ms-NNN` identifier is stable: `ms-241`
is the same node under either prefix. Those 30 pairs therefore identify 30 nodes,
not 60. This confirms physical identity, not which DNS endpoint currently exists
or whether both names resolve. Retain endpoint history and source timestamps.

The sheet's recorded **Current OS Installed** values are 154 Ubuntu 24.04,
78 Ubuntu 18.04, and 83 blank. All 65 Windows-prefixed rows have a blank OS cell;
prefixes alone do not establish installed OS. Recorded states are 234 `prod`,
67 `dev`, eight `out-of-service`, and six blank. These are inventory annotations,
not live observations. Neither `dev` nor `out-of-service` is an established rule
for omitting hardware from coverage or declaring it retired.

The maintainer subsequently confirmed on 2026-11-13 that seven Moonshot chassis
are currently racked, containing 315 nodes. The coverage population is therefore
all 315 nodes, including offline and imaging nodes; the 280-name baseline is
incomplete for that population. The 35 additional source slots must be accounted
for. This confirms the population count, not current reachability, installed OS,
or the freshness of each sheet row.

Until the projection is updated and validated, label reports against the existing
list as scoped to 280 nodes rather than complete coverage of the 315-node hardware
inventory. Updating a coverage denominator is separate from selecting operational
collection targets.

### Confirmed identity and DHCP/DNS rules

The maintainer confirmed on 2026-11-13:

- The numeric Moonshot identifier stays fixed across imaging: `ms-241` remains
  `ms-241`. Use `ms-NNN` as the logical node key within this Moonshot inventory.
- Windows-to-Linux migration changes the hostname prefix from `t-w1064` to
  `t-linux64`. The endpoint/domain change is performed with IT in its DHCP/DNS
  database; imaging alone does not establish that the new endpoint is valid.
- Linux 18.04-to-24.04 imaging keeps the existing Linux hostname.

Fleetroll should reconcile recorded names to one inventory node while retaining
each endpoint's evidence, original timestamps, and worker identity. It must not
invent a new collection/write target from a planned OS, assume IT completed a
rename, merge distinct Taskcluster registrations, or change DHCP/DNS itself.
Use an explicitly recorded endpoint for collection; surface source/observation
disagreements and unknown current naming. Chassis/cartridge/node-index remains
useful physical provenance; hardware replacement and number reuse still need
their separate lifecycle contract.

### Recorded hostname evidence

A subsequent read-only `host monitor --once --json` inspection compared all
315 inventory names plus the 30 additional baseline Linux-name candidates
(345 endpoint names, not 345 physical nodes). It made no live collection calls:

- Linux names 241–270: all 30 latest host gathers failed, with observation times
  from `2026-11-07T04:02:36+00:00` through `2026-11-07T04:02:46+00:00`.
  No historical worker identity was available for those names.
- Windows names 241–270: no recorded host observations or historical identities.
- The 35 additional source Windows names: no recorded host observations or
  historical identities.

These records cannot establish current reachability, OS, or which endpoint IT
currently registers. The stable numeric identity is confirmed separately by the
maintainer, not inferred from these failed or absent observations. Do not infer
65 additional machines by adding all Windows names to the existing 280-name list;
30 are alternate names for nodes already represented. The physical membership
gap is 35 nodes, and the coverage denominator is 315.

## Consequences for coverage work

- Preserve membership separately from the sheet's OS/state annotations, live
  collection results, and desired imaging target. The workbook has a current-OS
  column; it does not establish where intended future assignment is maintained.
- Account separately for a failed latest gather, stale last-success evidence,
  historical identity only, and no observations. Failure to reach a host is
  evidence at that attempt's time, not permanent offline status.
- Preserve original timestamps for retained OS/role/worker identity. Old Windows
  registrations cannot establish that an imaged Linux node is still Windows.
- Reconcile the confirmed Windows/Linux hostname forms by stable `ms-NNN` identity
  within this inventory, retaining endpoint-specific evidence. Do not use that
  grouping as proof of current DNS registration or authorization to write to
  another endpoint. Hardware replacement and hostname reuse still require
  lifecycle rules and history.
- Record source sheet/tab, export or sync time, and source revision/fingerprint
  when importing. Do not fabricate a source revision from a file modification
  time or treat a successful import as proof of source completeness.
- Hostname-range gaps remain diagnostics, not enrollment rules. In particular,
  the export contains suffixes 616–630; a continuous 001–630 range would invent
  many inventory members not established by the source.

At the maintainer-approved pause point, remaining baseline/report work in
`mvp-xs2c.1` is deferred behind the identity/source design in `mvp-xs2c.2`.
Completed list cleanup remains complete; the unfinished report is not closed.
The next task is the design contract for logical node, hardware, interfaces,
network assignments, lifecycle, provenance and uncertain associations.
Shared persistence is `mvp-xs2c.6`, inventory source import is `mvp-xs2c.9`, and
recorded endpoint association is `mvp-xs2c.10`, all under the existing epic.
See the [fleet membership design](../design/fleet-membership-and-discovery.md).
