## Why

data-parity assumes the user's dbt project can be parsed and queried on both engines, which in practice means the
models carry "dual engine" Jinja (`target.type` branches, `adapter.dispatch`, engine-specific `source()` wiring).
Most real projects do not: they were written for one engine (e.g. BigQuery) and the other side is a separate,
hand-made project or a plain copy of the tables. Today the tool gives no guarantee, and no clear error, in that case.
The project must work with a standard dbt project that has none of that Jinja.

## What Changes

- The only thing data-parity requires from a dbt project is: the `data_parity` package installed, models opted in
  (tag or `meta.data_parity`), and the tables already built on that engine. No model SQL, macro or `target.type`
  branch of the user's project may be required to compile on the other engine.
- Each side is resolved on its own project (`--project` / `--project-b`): model discovery, `ref()`, columns,
  partition column and primary key are read from that side's own graph and relation, never assumed identical.
- Main case: one repo whose models are the BigQuery ones (its `ref` / `source` point to `<project>.<dataset>`),
  compared with the same tables on Trino. The relation read on each side is resolved from a mapping file or a
  per-side namespace (`--schema-a` / `--schema-b`, `[database.]schema`, e.g. `<catalog>.<schema>` for Trino),
  falling back to the repo's own `ref()` / `source()`; models are parsed but never compiled or run on the foreign
  engine. No catalog, schema or dataset name is hard-coded.
- Columns can be excluded from the CLI, without editing the repo: `--exclude-column COL` and `--exclude-prefix P`
  (both repeatable, `--exclude-column` accepts `model.col` to target one model). The prefix `meta_` is excluded by
  default; `--no-default-exclude` turns that default off. Same exclusion on both sides, listed in the report.
- Models are matched across sides by a name that does not need dual-engine Jinja: same `name` by default, or an
  explicit `meta.data_parity.compare_as` key (covers renamed models / aliases such as `orders_dpv`).
- A model present or opted in on only one side is reported as a clear finding (not a crash, not a silent skip).
- Failures caused by a project that is not dual-engine (parse error on a side, unknown model, missing package) get an
  actionable message and a dedicated exit code path instead of "dbt produced no PARITY output".
- Docs: README, runbook and a new integration fixture proving a single-engine project works.

## Capabilities

### New Capabilities
- `single-engine-projects`: running reconcile / conformance when each side is an independent, non dual-engine dbt
  project; model matching across sides; per-side discovery; error reporting for one-sided or unparsable projects.

### Modified Capabilities

(none: `openspec/specs/` is empty, there is no existing spec to modify)

## Impact

- `src/data_parity/cli.py`, `drill.py`, `runner.py`, `report.py`: per-side model list, matching, one-sided findings,
  error messages.
- `dbt_package/macros/reconcile.sql`, `cache.sql` (model discovery, `compare_as`, `ref` robustness).
- `integration_tests/`: a new fixture with two projects without any `target.type` Jinja; `tests/` unit and
  integration tests.
- `README.md`, `docs/runbook.md`. No new dependency, no data leaves an engine (still aggregates only).
- Not breaking: dual-engine projects keep working unchanged.
