# SQLite migration benchmarks

Historical comparison from the JSONL-to-SQLite migration (`mvp-1k8.1`–`mvp-1k8.8`).
These measurements explain the migration; they are not current performance or
capacity guarantees. Later [compact configuration history](../reference/configuration-history.md)
adds storage and processing beyond this workload.

## Synthetic workload

The recorded setup used 679 hosts × 10 observations (6,790 records), approximately
627 bytes per record, a temporary filesystem, and medians over 3–5 runs.
JSONL appended records and scanned/parsing all lines for reads; SQLite used
`fleetroll/db.py`, indexed lookups, transactions, and retention enforcement.
Memory was measured with `tracemalloc`.

| Metric | JSONL | SQLite |
| --- | ---: | ---: |
| Single write | 0.03 ms | 0.25 ms |
| Batch of 100 writes | 3.09 ms | 1.44 ms |
| Bulk read, 679 hosts | 0.22 ms | 0.28 ms |
| Single-host read | 0.20 ms | 0.02 ms |
| Startup | 0.23 ms | 1.23 ms |
| Bulk-read peak memory | 0.20 MB | 0.06 MB |

SQLite traded single-write/startup overhead for indexed single-host reads, lower
measured memory, and automatic retention. Bulk reads were comparable in this
small retained dataset. Batch transaction results do not imply that every
production gather commits its observations in a single transaction.

## Historical JSONL files

| Dataset | Bulk read | Single-host read | Bulk-read peak memory |
| --- | ---: | ---: | ---: |
| 30 MB, 32,563 records | 110.0 ms | 95.0 ms | 2.2 MB |
| 172 MB, 202,733 records | 689.6 ms | 596.1 ms | 2.2 MB |

Full-file scans became expensive as JSONL accumulated history. Comparing these
single-host reads with the synthetic SQLite result illustrates the indexing
benefit, but mixes datasets; it does not establish a constant lookup time,
monitor refresh rate, or production speedup for every workload.

## Reproduce

```bash
uv run python tools/bench_sqlite_vs_jsonl.py
uv run python tools/bench_sqlite_vs_jsonl.py --real
```

`--real` uses JSONL files in `~/.fleetroll/` when available. Re-run on the target
environment before using the figures for capacity planning. Current retention
also preserves a last successful host observation when needed and separate
compact history; database size is not simply ten records per host.
