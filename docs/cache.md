# Closed-partition cache for pass 1

Pass 1 of data-parity scans every row of every table once (one grouped scan, `count`, `sum(h1)`, `sum(h2)` per
partition). On a multi-TB table that is the whole cost. A **closed** partition (older than a lag, default 3 days
before the cutoff date) does not change any more, so its fingerprint can be computed once and kept. Later runs
only scan the **open** partitions (and any closed partition the cache cannot vouch for).

Code: `dbt_package/macros/cache.sql` (logic), `cache_adapters.sql` (per-adapter pieces).
Test: `integration_tests/test_cache.sh` (see `integration_tests/README_cache.md`).

## Macros

| Macro | What it does |
|---|---|
| `data_parity_cache_refresh(only, lag_days=3, reference_date, schema, write=false)` | Finds closed partitions that are missing from the cache or stale, computes them live and stores them. **Dry run unless `write: true`.** |
| `data_parity_cached_pass1(only, lag_days=3, reference_date, schema)` | Prints the pass-1 contract lines (no `as_of` / `partitions`: passing either is a compiler error, use `data_parity_reconcile` for a scoped pass 1; a NULL partition is printed `part|\N`, like reconcile). Closed partitions come from the cache, open / untrusted ones are scanned live. Never writes. |
| `data_parity_cache_verify(only, sample=5, schema, fail_on_stale=true, lag_days, reference_date)` | Recomputes N random cached partitions live and prints `VERIFY|OK|...` or `VERIFY|STALE|...`. Fails the run on STALE unless `fail_on_stale: false`. |

```
dbt run-operation data_parity_cache_refresh --args '{schema: dp_cache}'               # dry run: lists what it WOULD do
dbt run-operation data_parity_cache_refresh --args '{schema: dp_cache, write: true}'  # creates schema/table, fills closed partitions
dbt run-operation data_parity_cached_pass1  --args '{schema: dp_cache}' | sed -n 's/.*PARITY|//p' > side.txt
dbt run-operation data_parity_cache_verify  --args '{schema: dp_cache, sample: 10}'
```

`reference_date` (default: current date) and `lag_days` define the cutoff: partition `< reference_date - lag_days` is closed.
(`reference_date` here is a date used to compute the cutoff; it is not the row filter of `data_parity_reconcile`.)

## Output

`PARITY|` lines are the pass-1 contract and are **byte-identical** to a full live pass 1 (tested against an
independent plain grouped query and, when installed, against `data_parity_reconcile` level 1, including its
`pk|` / `note|` lines). Everything informative uses another prefix so that it never reaches a diff:
`NOTE|...` (what came from the cache, what was scanned live, and why) and `VERIFY|...`.
`sed -n 's/.*PARITY|//p'` extracts the contract only.

## Safety defaults

- **No write unless `write: true`.** The default run creates nothing and reports missing / stale partitions.
- **No default schema.** The cache schema is the `schema` argument or var `data_parity_cache_schema`; if neither
  is set, `write: true` and `cached_pass1` refuse to run. Nothing is ever written to a production dataset by default.
- **Trust rule.** A cached row is trusted only if its `schema_fp` and `version` equal the current ones. Otherwise the
  partition is scanned live (and, with `write: false`, not stored). The cached pass 1 can therefore not be wrong
  because of a schema change or a version bump; only a silent rewrite of a closed partition can fool it (below).
- **Only aggregates are stored**: counts and sums of hashes, plus the partition value (a date), the model name and
  a hash of the column-name/type list. No row, no key, no business value.
- BigQuery: the cache dataset is never created by the macro (location and ACLs are a human decision); it must exist.

## Cache table

`<schema>.data_parity_cache(model, partition_value, rows_n, h1_sum, h2_sum, schema_fp, version, computed_at)`,
one table per engine. `partition_value` is the partition as text (`YYYY-MM-DD`, the partition expression is
`cast(col as date)`). `schema_fp` = md5 of `spec1;<name>:<type_class>,...` over the kept columns (sorted,
`meta_` excluded): adding, dropping, renaming or retyping a column changes it, and so does a change of
canonical-form version (`spec1`). The table is created `if not exists`.

The table is **append-only by default**: when a partition is recomputed a new row is inserted and the newest row
(`computed_at`) wins on read. Reason: not every connector supports row `DELETE` (Trino `memory` does not, found
while testing). Set var `data_parity_cache_prune: true` to delete the superseded rows before inserting
(needs a connector with DELETE, e.g. DuckDB, Iceberg, BigQuery).

## Invalidation rules

1. **Schema change** (column added / removed / renamed / type class changed): `schema_fp` differs, every cached
   partition of that model is recomputed. Tested (DuckDB and Trino).
2. **Version var**: `--vars '{data_parity_cache_version: "2"}'` (default `'1'`) invalidates everything. Use it
   after a change of the hash definition, after a known backfill, or to force a full re-fingerprint. Tested.
3. **Rewrite of a closed partition** (late data older than the lag, a backfill, a fix): the cache cannot see it.
   `data_parity_cache_verify` recomputes a random sample of cached partitions and reports `STALE` when they no
   longer match. Run it regularly (it costs `sample` partitions per model). Tested: a rewrite of one closed
   partition is **not** visible in the cached pass 1 (that is the risk), and verify finds it. Recovery: remove
   the stale row(s) from the cache table and refresh, or bump the version.
   Verify is a sample, not a proof: with 1 stale partition out of 100 and `sample: 5` the chance to catch it
   in one run is about 5 %. Use a large sample after a known backfill, or bump the version.
4. **Late data window = `lag_days`**: data arriving within `lag_days` of its partition date is picked up because
   those partitions are still open and always scanned live. Choose `lag_days` at least as large as your
   longest normal late-arrival delay. Data arriving later than that is exactly case 3.
5. Partitions that exist in the cache but no longer exist live are ignored (not printed), as a live scan would.
6. A model without partition column is never cached (always a full live scan, note printed).

## Cost model

- One-off: the first `refresh --write` scans all closed partitions once (same cost as one full pass 1).
- Later runs: `cached_pass1` costs about the open partitions only (`lag_days` + newer days) plus a scan of the
  partition column alone to list the closed days, plus reading the small cache table. For daily partitions and
  `lag_days: 3` over 3 years that is about 0.3 % of the data instead of 100 %.
- Refresh after the first one: only partitions that closed since the previous refresh (about one day per day).
- verify: `sample` partitions per model.
- Measured on the DuckDB fixture (`orders`, 120 days, reference_date 2025-04-10): 96 closed partitions served from the
  cache, 24 scanned live; the compiled SQL (dbt-core `logs/dbt.log`) shows the only hash scan filtered with
  `"order_date" >= date '2025-04-07'`.
- The cache is tiny: one row per model and day (about 100 bytes).

On BigQuery the partition predicates are written as `col >= date '...'` / `col in (date '...')` / `col < date '...'`
on the raw column when it is of type DATE, so partition pruning can apply. For TIMESTAMP columns the expression is
`cast(col as date)`, which may not prune: prefer a DATE partition column.

## Per-adapter status

| Adapter | Status |
|---|---|
| DuckDB | tested (Fusion 2.0.9 and dbt-core 1.12 / dbt-duckdb) |
| Trino | tested on the `memory` connector with dbt-core (dbt Fusion has no Trino): dry run, refresh, cached pass 1, verify, schema change, version bump. Row `DELETE` is not supported there, hence append-only default |
| BigQuery | **UNTESTED, never executed.** `bigquery__` implementations in `cache_adapters.sql` (fqn with backticks, `string`/`int64`/`timestamp` types, `date_sub(.., interval n day)`, `rand()`, no automatic dataset creation) were written from the documentation only. The shared SQL (date literals, `insert ... values`, `delete`, `current_timestamp`) is standard but unrun. Run `refresh` as a dry run first, then on a scratch dataset, before trusting it |

## Limits (honest)

- The cache trusts closed partitions. Only `cache_verify` (sampling) and the version var protect against silent
  rewrites. A byte-identical diff with a stale cache on both sides can hide a corruption present in both caches
  at the time they were computed (the cache stores what was there then).
- Partition column must be a DATE, a TIMESTAMP, or castable to date. Integer / string partitions are not supported.
  Timestamps are cut at UTC midnight on BigQuery and in the session time zone on DuckDB `timestamptz`.
- The list of partitions to scan is passed as an `IN (date, ...)` literal list; hundreds are fine, tens of
  thousands are not (the first full refresh uses a range predicate instead of a list).
- No locking: two concurrent refreshes may both insert a partition (harmless, newest row wins, but wasted work).
- Refresh is not atomic across partitions (rows are inserted in batches of 500).
- `NULL` partition rows are always scanned live and printed as `part|NULL|...`.
- `data_parity_cache_verify` samples among rows matching the current `schema_fp` and `version`.
- The `pk|` / `note|` lines are mirrored from `data_parity_reconcile` only if that macro is installed
  (`data_parity_rc_pk is defined`); otherwise they are omitted. If its output format changes, update `cache.sql`.
