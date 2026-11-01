## Context

data-parity runs `dbt run-operation data_parity_reconcile` once per side, each in its own `Side(project, target)`
(`runner.py`), and diffs the `PARITY|` lines. `--project-b` already allows two projects, and
`integration_tests/trino_project/` is a hand-made second project (models are `select * from <db>.<schema>.<table>`,
aliased `*_dpv`). Model discovery (`data_parity_rc_models`, `cache.sql`) walks `graph.nodes` of the project being run,
and the engine-specific SQL lives only in the package (`adapter.dispatch`), never in the user's project.

So the package itself is already engine-neutral. What is not guaranteed is everything that assumes the *user's*
project is dual-engine: same model names and tags on both sides, both projects parse, `ref()` resolves, the other
side's graph looks like the first. None of this is specified or tested, and failures surface as the generic
"dbt produced no PARITY output".

## Goals / Non-Goals

**Goals:**
- A standard dbt project (no `target.type` branch, no custom dispatch, no shared source wiring) works on each side.
- Clear, per-side, actionable errors and findings when the two projects do not line up.
- Proven by an integration fixture with two independent projects.

**Non-Goals:**
- Generating or translating the user's models for the other engine.
- Comparing tables that are not dbt models (raw tables with no dbt node).
- Changing the hash, the canonical form or the report layout for matching models.

## Decisions

- **Per-side discovery, matching in Python.** Each side prints its own `## <model>` blocks; the CLI matches them by
  key. Alternative: pass side A's model list to side B via `only`. Rejected: it forces B to know A's names and
  turns a missing model into a compiler error instead of a finding.
- **Match key = `meta.data_parity.compare_as`, else the model `name`.** The key is emitted in the `## ` header line
  so the diff stays byte-based. Alternative: match by alias or relation name. Rejected: aliases (`orders_dpv`) are
  per-engine implementation details and relations differ by database/schema.
- **One-sided models are findings, not errors.** Reported as `only_in_A` / `only_in_B` in `parity.json` and the
  Markdown report; exit code 1 (differences), consistent with a mismatch. Alternative: warn and skip. Rejected:
  silently ignoring a model defeats the purpose of the tool.
- **Per-side column/partition/PK resolution is already local** (`adapter.get_columns_in_relation(ref(...))`); keep
  it, and add a cross-side check that the compared column sets match, reported as a schema mismatch for that model.
- **Error classification in `runner.py`.** Parse the dbt output for the known failure shapes (project parse error,
  `Compilation Error` on a model, package not installed, `ref` not found) and surface them in `CallResult.reason()`
  with the side and the fix. Alternative: always show the tail. Kept as fallback.

- **The models come from the user's dbt repo, whatever its native engine; each side is that repo run on a target.**
  Real case: the repo is the BigQuery one (its `ref`/`source` resolve to `<project>.<dataset>`), side A runs it with
  the BigQuery target, side B runs the same repo with the Trino target (dbt-trino) and reads the Trino table.
  Models are parsed on both targets, never compiled or run on the foreign one.
- **Relation = namespace + table identifier, per side, symmetric.** Resolution order per model and per side:
  (1) mapping file entry for the model, (2) the side's namespace (`--schema-a` / `--schema-b`) + table identifier,
  (3) the project's own `ref()` / `source()` relation on that target. Namespace options are symmetric: a side
  that reads the repo's own relations needs none, a side on another engine needs one. Here: `--schema-b
  <catalog>.<schema>` for Trino. `meta.data_parity.relations` stays optional, not the main path.
- **Namespace is `[database.]schema`.** Trino: `<catalog>.<schema>`; BigQuery: `<project>.<dataset>` or
  `<dataset>`. The side's adapter quotes it (`api.Relation.create(database, schema, identifier)` +
  `adapter.quote`), so no engine syntax lives in the CLI. A quote, backslash or more than two segments is refused.
- **Table identifier = the identifier of the model in the repo** (source `identifier:`, model alias, else name), the
  same on both sides unless the mapping file overrides it. Column names are matched separately (see below), so a
  Trino table whose columns use `/` where the repo's BigQuery columns use `_` still lines up.
- **Models are parsed, never compiled, on the foreign target.** `run-operation` only needs the graph, so BigQuery-only
  SQL in model bodies is never sent to Trino. Task 1.1 verifies that parse alone succeeds on the Trino target
  (macros called at parse time, `source()` with a BigQuery `database`, a missing dbt-trino profile are the suspects).

- **Roles are independent of A/B.** Which side is the legacy one is an option, `--legacy a|b` (default `b`), not
  a property of the letter. Real run: `--a bq --b trino`, so A is the migration target (BigQuery, from
  the repo's own relations) and B the legacy side (Trino, gets `--schema-b <catalog>.<schema>`). The report words
  one-sided models by role: legacy only = "not migrated yet"; target only = "no legacy counterpart". Labels via
  `--label-a` / `--label-b` (default: the target names). The hash logic stays symmetric.
- **Unpartitioned tables are first-class.** A model with no partition column (no `partition_column` and no
  `event_time`) is compared with the existing `*` partition: pass 1 prints the table line only, drilling goes
  straight to buckets (pk) or the whole table (no pk), as the macros already do. `--last N`, `--as-of` and the
  cache only apply to partitioned models and are reported as "not applied" for the others, never as an error.
  A partition column present on one side only is a schema mismatch.

- **Columns are matched on a normalized name.** The match name of a column is its lower-cased name with every
  character outside `[a-z0-9_]` replaced by `_` (Trino `a/b` and BigQuery `a_b` are the same column). The macros sort
  the columns, build the hash parts, print the `cols|` line, the pass-3 `col|` lines and the exclusions on this
  normalized name, so both sides use the same order and the same labels. This matters for the hash: the row hash
  depends on the column order, and `/` and `_` do not sort the same way, so comparing raw names would give different
  hashes for identical data. Exclusions (`--exclude-column`, `exclude_columns`) are also matched on the normalized
  name. Only the name is normalized, never a value. Original names stay available in the report when they differ.
  Alternative: an explicit column mapping file. Kept as a possible later addition, not the default, since the
  separator case is systematic. Two columns of one side normalizing to the same name is a compiler error naming both.

## Risks / Trade-offs

- [dbt `run-operation` parses every model, so an engine-specific model could fail parse on the wrong engine] →
  Only an issue when one project is used on both sides; the documented path for single-engine projects is
  `--project-b`. Task 1.1 reproduces this first to confirm which failures are real before coding.
- [`compare_as` collisions] → reject duplicates within one side with a compiler error naming both models.
- [Header format change could break `report RUN_DIR` on old runs] → keep the old header valid; `compare_as` is only
  appended when set.

## Open Questions

- Resolved: the target case is ONE repo whose models are the BigQuery ones (`ref`/`source` resolve to
  `<project>.<dataset>`), also run with a Trino target whose table is addressed by `--schema-b`. Two separate
  projects (`--project-b`) stay supported.
- Should one-sided models fail the run (exit 1) or only warn behind a flag?

- **Nothing is hard-coded.** No catalog, schema, dataset or project name exists in the code or the package: the
  namespace comes only from `--schema-a` / `--schema-b`, the mapping file or the project's own `ref()` / `source()`.
  Examples in this change use `<catalog>.<schema>` placeholders. With no option, behaviour is unchanged.

## Reproduction (task 1.1)

Setup: `integration_tests` (dbt Fusion 2.0.9, DuckDB), side B profile with `schema: dp_elsewhere` while the tables are in
`dp_out`. Result with the current code:
- The project is parsed fine with the foreign profile (graph available): hypothesis "parse fails on the other
  engine" is NOT confirmed for this setup. Still to check with a real BigQuery/Trino pair.
- `ref()` resolves to the profile schema, the relation is empty, `get_columns_in_relation` returns no columns, and
  the run dies with the misleading `partition column order_date not found (or excluded)`.
- Consequence for the design: an unresolved relation must be detected explicitly (no columns -> "relation
  <name> not found on side X, check --schema-X / mapping") before any column logic.
