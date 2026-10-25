# data-parity integration test bed

Two simulated engines (two DuckDB files, schema `mart`) holding the same logical tables (`orders` ~200k rows
partitioned by `order_date`, `customers` ~5k, `events_nopk` ~20k without primary key). `engine_b` carries deliberate
corruptions listed in [expected_findings.md](expected_findings.md) (the ground truth).

Regenerate the data (deterministic, git-ignored `data/`; any Python with duckdb):

    python3 integration_tests/generate.py

Run dbt with `cwd = integration_tests/` (the DuckDB paths in `profiles.yml` are relative to it):

    cd integration_tests
    dbt deps
    dbt run-operation data_parity_conformance --target engine_a | sed -n 's/.*PARITY|//p'

The conformance output (79 lines of synthetic rows) must be byte-identical on every engine.
