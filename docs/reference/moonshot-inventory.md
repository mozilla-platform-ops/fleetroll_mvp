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
`t-w1064-ms-NNN.wintest.releng.mdc1.mozilla.com` in the export. They are rename
candidates, not verified aliases. The baseline contains no chassis/slot mapping
with which to independently prove that identity. Do not probe or mutate both
names as if they were two different physical nodes, or rewrite one to the other
without an explicit reconciliation rule.

The sheet's recorded **Current OS Installed** values are 154 Ubuntu 24.04,
78 Ubuntu 18.04, and 83 blank. All 65 Windows-prefixed rows have a blank OS cell;
prefixes alone do not establish installed OS. Recorded states are 234 `prod`,
67 `dev`, eight `out-of-service`, and six blank. These are inventory annotations,
not live observations. Neither `dev` nor `out-of-service` is an established rule
for omitting hardware from coverage or declaring it retired.

The sheet is therefore the identified inventory source, but equivalence of its
315-row population and the current 280-name Fleetroll projection is unverified.
The maintainer must confirm whether the 35 missing numeric slots are in scope or
whether the baseline intentionally represents a subset. Until that is resolved,
keep the existing lists unchanged and report any coverage explicitly against
the supplied baseline, not as complete coverage of the Google inventory.

## Consequences for coverage work

- Preserve membership separately from the sheet's OS/state annotations, live
  collection results, and desired imaging target. The workbook has a current-OS
  column; it does not establish where intended future assignment is maintained.
- Account separately for a failed latest gather, stale last-success evidence,
  historical identity only, and no observations. Failure to reach a host is
  evidence at that attempt's time, not permanent offline status.
- Preserve original timestamps for retained OS/role/worker identity. Old Windows
  registrations cannot establish that an imaged Linux node is still Windows.
- Reconcile hostnames through explicitly reviewed physical identity rules.
  Chassis/cartridge/node-index is a useful source key; hardware replacement and
  hostname reuse still require lifecycle rules and history.
- Record source sheet/tab, export or sync time, and source revision/fingerprint
  when importing. Do not fabricate a source revision from a file modification
  time or treat a successful import as proof of source completeness.
- Hostname-range gaps remain diagnostics, not enrollment rules. In particular,
  the export contains suffixes 616–630; a continuous 001–630 range would invent
  many inventory members not established by the source.

The remaining baseline/report work is `mvp-xs2c.1`. Formal source ownership,
identity, inclusion/lifecycle, and import contracts belong to `mvp-xs2c.2`;
persistence follows in `mvp-xs2c.6`. See the
[fleet membership design](../design/fleet-membership-and-discovery.md).
