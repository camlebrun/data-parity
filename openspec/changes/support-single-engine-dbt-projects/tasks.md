## 1. Reproduce and fixture

- [x] 1.1 Reproduce on `integration_tests/` + `integration_tests/trino_project/`: list what fails when a project has no dual-engine Jinja (3 hypotheses, cheapest command each) and record the result in design.md
- [x] 1.2 Add a fixture of ONE repo (source() with its own schema) run on a second DuckDB target whose tables are in another schema, addressed by `--schema-b`; then a fixture of two independent DuckDB projects with no `target.type` / dispatch Jinja, one model renamed via `compare_as`, one model one-sided

## 2. Package (dbt macros)

- [x] 2.1 Emit the match key (`compare_as` else name) in the `## ` header of `reconcile.sql` and `cache.sql`, reject duplicate keys per side
- [x] 2.2 Grep all callers of `data_parity_rc_models` and `ref(node.name)` before editing; keep `only` working on names and keys

- [x] 2.3 Add `data_parity_rc_relation(node)`: mapping entry, then side namespace `[database.]schema` + model table identifier, then the repo's own `ref`/`source` (e.g. BQ `<project>.<dataset>.orders` -> Trino `<catalog>.<schema>.orders`) and use it in reconcile, cache and conformance-adjacent code paths
- [x] 2.4 Validate namespace (1-2 segments) and mapping entries; refuse quotes/backslashes with a compiler error
- [x] 2.5 Mapping file (`--mapping-a` / `--mapping-b`, `model: [database.]schema.table` lines, no YAML dependency), read by the CLI (`mapping.py`) and passed as the `relations` macro argument, not `--vars`

- [x] 2.6 Normalize column names (lowercase, non `[a-z0-9_]` -> `_`) in `reconcile.sql` and `cache.sql` before sorting, hashing, exclusions, `cols|` and `col|` lines; error on collision within a side (grep every use of `lname` and `sort(attribute='name')` first)
- [x] 2.7 Keep the original column name for the SQL (quoted with `adapter.quote`) and the normalized one for matching and output

## 3. CLI

- [x] 3.1 Match models across sides in `drill.py` by key; compute only_in_A / only_in_B
- [x] 3.2 Column-set comparison per matched model, schema-mismatch status
- [x] 3.3 Classify dbt failures in `runner.py` (`CallResult.reason`) per side: missing package, parse error, unresolvable ref

- [x] 3.4 Pass the default dataset per side to the macro (new `--schema-a` / `--schema-b`, sent as the `rel_namespace` macro argument, not `--vars`) and document precedence (mapping, then `--schema-*`, then `ref()`: in the `--help` of the option; README in 5.3)

- [x] 3.5 `--exclude-column` (`col` or `model.col`), `--exclude-prefix` (repeatable), `--no-default-exclude`; reject empty values; pass them in the macro args for both sides
- [x] 3.6 Macros (`reconcile.sql`, `cache.sql`): merge CLI exclusions with meta (grep every use of `exclude_columns` / `exclude_prefixes` first); keep the `meta_` default

## 4. Report

- [x] 4.1 Report and `parity.json`: one-sided models and schema mismatches; exit code 1; `report RUN_DIR` still reads old runs

- [x] 4.2 Wording by role in the report (legacy vs target), `--legacy a|b` (default b), `--label-a` / `--label-b`
- [x] 4.3 Report note for unpartitioned models when `--last` / `--as-of` / cache cannot apply

## 5. Tests and docs

- [x] 5.1 Unit tests (unpartitioned model, column `a/b` vs `a_b` gives the same hash and order, collision error, matching, duplicates, error classification, exclusions: default `meta_`, CLI column, `model.col`, `--no-default-exclude`) in `tests/test_units.py`
- [x] 5.2 Integration test on the new fixture in `tests/test_integration.py`
- [x] 5.3 README and `docs/runbook.md` (French): single-engine projects, `compare_as`, `--project-b`
