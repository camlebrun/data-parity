# Cache tests (`test_cache.sh`)

Design: [../docs/cache.md](../docs/cache.md). These shell tests cover the cache macros (the Python CLI does not use the cache); run them after any change to `dbt_package/macros/cache*.sql`. The cache's date argument is `reference_date` (closed-partition cutoff = reference_date - lag_days); it is unrelated to `as_of` of `data_parity_reconcile`.

    PY=<python with duckdb> DBT=dbt DBT2=<dbt-core, optional> bash integration_tests/test_cache.sh
    # + Trino (needs the dp-trino container on :18080, dbt-core with dbt-trino, python with `trino`):
    TRINO=1 PY=<python with duckdb+trino> DBT2=<dbt-core> bash integration_tests/test_cache.sh

The script works in a temp dir (`KEEP=1` keeps it). It COPIES `data/engine_a.duckdb` and `engine_b.duckdb`; the
originals are never written. Nothing touches BigQuery.

| Step | Proof |
|---|---|
| reference | independent macro `ref_pass1` (one plain grouped query with `data_parity_row_hash_parts`) |
| (a) | dry run: output lists WOULD-insert partitions, schema `dp_cache` does not exist afterwards; `write: true` without schema is refused |
| (b) | `write: true`: 192 rows (96 closed days x orders, events_nopk), max partition < cutoff |
| (c) | `cached_pass1` byte-identical to the reference (Fusion and dbt-core) and to `data_parity_reconcile` level 1 |
| (d) | second refresh: "up to date", row count unchanged |
| (e) | rewrite of one closed day in the engine_b COPY: cached pass 1 still returns the OLD data (the danger), `cache_verify` reports exactly 1 STALE and 191 OK, and fails the run |
| (f) | new column / `data_parity_cache_version` bump: nothing trusted, output still identical to live, refresh recomputes |
| (g) | dbt-core `logs/dbt.log`: the only hash scan of the partitioned tables is `where order_date >= date '2025-04-07'` (open days) |
| Trino | same (a)-(f) on a tiny table in `memory.dp_cache_src` (the fixture loader `memory.dp_a/dp_b` was not present when written) |

Notes: `DBT2` (dbt-core) is only needed for (g), which reads the compiled SQL from its log; `PY` is needed
to mutate the copy. A macro of the root project cannot call package macros in this setup, so the reference
macro is added to a copy of the package in the temp dir.
