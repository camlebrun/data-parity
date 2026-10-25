# Reconcile tests

    PY=<python3> DBT_CORE=<dbt-core binary> TRINO=1 sh integration_tests/test_reconcile.sh

`PY` runs the CLI (`PYTHONPATH=src python -m data_parity run ...`, stdlib only; default `python3`); with `TRINO=1` it
must also have `duckdb` and `trino` (for `load_trino.py`), or set `CORE_PY` for the loader only. `DBT_FUSION` defaults
to `dbt`. This script covers the dbt-core and Trino matrix; `tests/test_integration.py` (pytest) covers the Fusion /
single-binary case. Run this one after changes to `dbt_package/`, pytest after changes to `src/`.

Assertions read the exit code and the `parity.json` written by the tool (plus a few Markdown spot checks); each
assertion fails if its file is missing.

Scenarios (ground truth: `expected_findings.md`): Fusion DuckDB A vs B (exit 1, the 5 orders days with their nature
and columns, customers `city`, events_nopk `value`); engine_a vs itself (iso control, exit 0); error exit codes (unknown
target and misspelled model give 2); `--last 5` on orders gives exit 0 with `scope_limited` true and the verdict
"ISO on the checked scope" while the whole-table run exits 1; compiled SQL shows one `group by dp_p` for pass 1;
dbt-core gives the same per-model results as Fusion; with `TRINO=1` (Docker Trino on
localhost:18080, catalog `memory`): `load_trino.py` loads the same FAKE fixtures into `memory.dp_a` / `memory.dp_b`,
`trino_project/` (dbt-core only) exposes them as views `<name>_dpv`, then DuckDB engine_a vs Trino dp_a must be iso and
vs Trino dp_b must give the same per-model results as DuckDB A vs B. BigQuery is NOT loaded (no writes): it stays validated by
`data_parity_conformance` only.

Type mapping used by `load_trino.py`: BIGINT->bigint, VARCHAR->varchar, DATE->date, DECIMAL(p,s)->decimal(p,s),
DOUBLE->double, BOOLEAN->boolean, TIMESTAMP->timestamp(6); anything else aborts. No type failed to map identically.

`trino_project` installs the package from the local path with `dbt deps` (the test script runs it); run it with
`--profiles-dir trino_project`.
