# data-parity integration test bed

Two simulated engines (two DuckDB files, schema `mart`) holding the same logical tables
(`orders` ~200k rows partitioned by `order_date`, `customers` ~5k, `events_nopk` ~20k without pk).
`engine_b` carries deliberate corruptions listed in [expected_findings.md](expected_findings.md)
(the ground truth: a correct reconciliation must flag exactly the "YES" rows and ignore the "NO" rows).

## Regenerate the data (deterministic, git-ignored `data/`)

    uv run --no-project --with duckdb python integration_tests/generate.py
    # or any python with duckdb: python3 integration_tests/generate.py

This also rewrites `expected_findings.md`.

## Run dbt (cwd = integration_tests/, `profiles.yml` paths are relative to it)

    cd integration_tests
    dbt deps
    dbt run  --target engine_a
    dbt run  --target engine_b
    dbt test --target engine_a          # unique tests on pks
    # compare with the CLI (stdlib-only Python; from a clone: PYTHONPATH=../src python -m data_parity ...)
    data-parity conformance --a engine_a --b engine_b
    data-parity run --a engine_a --b engine_b     # exit 1: the corruptions are found; report path printed

The same commands work in PowerShell (set `$env:PYTHONPATH = "..\src"` when the package is not installed).

Always `cd integration_tests` first: the DuckDB paths are relative to the cwd (verified: running from `/` with
`--project-dir/--profiles-dir` fails to open the data files).

Models `orders`, `customers`, `events_nopk` are tagged `data_parity` (under `config:` for dbt Fusion),
`event_time: order_date` on the dated ones. Models are materialised as tables in schema `dp_out`
inside each engine file; the source data stays in `mart`.

## What a correct reconciliation must reproduce

See `expected_findings.md`. In short: orders differs on exactly 5 days (missing rows, amount +0.01,
`'paid '` status, price_float +0.01, extra row); the float-noise day and the re-ordered day are NOT flagged;
`meta_loaded_at` is ignored; customers has 1 NULL-vs-'' difference; events_nopk differs on 1 day.

## Which test to run when

- `python -m pytest -q` (repo root; `DBT_FUSION=dbt` or `DBT_CORE=<dbt>` adds `tests/test_integration.py` on these
  fixtures): after any change to `src/data_parity/`.
- `sh integration_tests/test_reconcile.sh`, `bash integration_tests/test_cache.sh`,
  `bash integration_tests/test_config_guards.sh`: the full matrix (dbt-core, Trino, cache, config guards), after any
  change to `dbt_package/` and before a release. See `README_reconcile.md` and `README_cache.md`.
