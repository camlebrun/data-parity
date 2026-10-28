## ADDED Requirements

### Requirement: No dual-engine Jinja required in the user's project
The system SHALL run `conformance` and `run` against dbt projects whose models, macros and sources contain no
`target.type` branch, no `adapter.dispatch` and no wiring shared with the other engine. The only requirements on a
project SHALL be the `data_parity` package, the opt-in (tag or `meta.data_parity`) and tables already built.

#### Scenario: Plain project on each side
- **WHEN** side A is a BigQuery-only project and side B a DuckDB-only project, both without engine-specific Jinja, and `data-parity run --project A --project-b B` is executed
- **THEN** both sides produce `PARITY|` output and the run ends with a verdict, with no error about the other engine

#### Scenario: Dual-engine project unchanged
- **WHEN** one dual-engine project is used on both sides
- **THEN** the behaviour and the output are identical to before this change

### Requirement: Per-side model discovery and matching
The system SHALL discover the opted-in models on each side from that side's own project, and SHALL match them across
sides by `meta.data_parity.compare_as` when set, otherwise by model name.

#### Scenario: Renamed model matched explicitly
- **WHEN** side A has model `orders` and side B has model `orders_dpv` with `compare_as: orders`
- **THEN** both are compared as `orders` and the report names it `orders`

#### Scenario: Duplicate match key on one side
- **WHEN** two opted-in models of the same side resolve to the same match key
- **THEN** the run fails with an error naming both models and the key

### Requirement: One-sided models are reported
The system SHALL report a model opted in on one side only as a finding in the Markdown report and in `parity.json`,
and the run SHALL NOT crash nor silently skip it.

#### Scenario: Model missing on side B
- **WHEN** `customers` is opted in on side A and has no matching model on side B
- **THEN** the report lists `customers` as only in A and the exit code is 1

#### Scenario: Legacy Trino vs BigQuery target
- **WHEN** side A is the BigQuery target, side B the legacy Trino side (`--legacy b`), and `orders` exists only on B
- **THEN** the report says `orders` is in the legacy side only (not migrated yet)

### Requirement: Compared schemas must line up
The system SHALL compare, per matched model, the set of column names (after exclusions) of both sides and SHALL
report a schema mismatch for the model instead of a hash difference when they differ.

#### Scenario: Extra column on one side
- **WHEN** side B has a column `loaded_at` that side A lacks and it is not excluded
- **THEN** the model is reported as a schema mismatch naming `loaded_at`, not as a data difference

### Requirement: Actionable errors for projects that cannot run
The system SHALL turn a failure caused by a project that does not parse, a missing package or an unresolvable `ref`
into a message that names the side, the cause and the fix, instead of the generic "no PARITY output" text.

#### Scenario: Package not installed on side B
- **WHEN** side B's project has no `data_parity` package and `dbt run-operation` fails with the macro-not-found error
- **THEN** the message states side B, that the package is missing, and to run `dbt deps` in that project

#### Scenario: Project does not parse
- **WHEN** a side's `dbt run-operation` exits with a parse or compilation error
- **THEN** the message names that side and shows the offending model or file from the dbt output

### Requirement: Relation resolved per side from a namespace
The system SHALL resolve the relation compared for each model on each side from, in order: an entry of the mapping
file, the side's namespace (`[database.]schema`, option `--schema-a` / `--schema-b`) combined with the table
identifier used by the project's own relation, then the project's own `ref()` / `source()` relation. Models SHALL NOT
be compiled or run on the foreign target.

#### Scenario: BigQuery repo compared with Trino
- **WHEN** the repo's `orders` resolves to `<project>.<dataset>.orders` on the BigQuery target, side A is that target, side B is the Trino target and `--schema-b <catalog>.<schema>` is given
- **THEN** side A reads `<project>.<dataset>.orders`, side B reads `<catalog>.<schema>.orders`, and the model SQL is never run on either side

#### Scenario: Namespace without database part
- **WHEN** `--schema-b <schema>` is given
- **THEN** side B reads `<schema>.orders` in the target's default catalog

#### Scenario: Table renamed on one side
- **WHEN** the mapping file of side B maps `orders` to `<schema>.orders_v2`
- **THEN** side B reads `<schema>.orders_v2` for the model `orders`, whatever `--schema-b` says

#### Scenario: Unsafe or malformed namespace
- **WHEN** a namespace has a quote, a backslash or more than two segments
- **THEN** the run fails before calling dbt, naming the option

### Requirement: Columns excluded from the CLI
The system SHALL let the user exclude columns with `--exclude-column` (`col` or `model.col`) and `--exclude-prefix`,
both repeatable, applied identically on both sides and combined with the model's `meta.data_parity` exclusions. The
prefix `meta_` SHALL be excluded by default, unless `--no-default-exclude` is given. Excluded columns SHALL be
listed per model in the report.

#### Scenario: Default meta_ prefix
- **WHEN** a model has columns `id`, `amount` and `meta_loaded_at` and no exclusion option is given
- **THEN** `meta_loaded_at` is excluded on both sides and listed as excluded for that model

#### Scenario: Column excluded from the CLI
- **WHEN** `--exclude-column updated_at` is given
- **THEN** `updated_at` is excluded from the hash on both sides, in every model that has it

#### Scenario: One model only
- **WHEN** `--exclude-column orders.updated_at` is given
- **THEN** `updated_at` is excluded in `orders` only

#### Scenario: Default turned off
- **WHEN** `--no-default-exclude` is given and a column is named `meta_loaded_at`
- **THEN** `meta_loaded_at` is part of the hash

#### Scenario: Empty prefix
- **WHEN** `--exclude-prefix ""` is given
- **THEN** the run fails before calling dbt with an error naming the option

### Requirement: Unpartitioned tables
The system SHALL compare models that have no partition column, on both sides, with a table-level comparison and the
drill-down by bucket, and SHALL NOT fail because `--last`, `--as-of` or the cache cannot apply to them.

#### Scenario: Table without partition column
- **WHEN** a matched model has neither `partition_column` nor `event_time` on both sides
- **THEN** it is compared as a whole table (partition `*`) and the report states that no partition scope applies

#### Scenario: `--last` with a mix of models
- **WHEN** `--last 30` is given and one model is partitioned and another is not
- **THEN** the partitioned one is limited to its 30 latest partitions, the other is compared whole, and the report says so

#### Scenario: Partition column on one side only
- **WHEN** a partition column exists for the model on side A but not on side B
- **THEN** the model is reported as a schema mismatch, not as a data difference

### Requirement: Column names matched on a normalized form
The system SHALL match columns across sides on their lower-cased name with every character outside `[a-z0-9_]`
replaced by `_`, and SHALL use that name for column ordering, hashing, exclusions and output, so that a column named
`a/b` on one engine and `a_b` on the other is the same column. Values SHALL NOT be altered.

#### Scenario: Separator differs between engines
- **WHEN** side B (Trino) has a column `order/id` and side A (BigQuery) has `order_id`, with identical data
- **THEN** both are the same column, the hashes are equal and no schema mismatch is reported

#### Scenario: Column order unaffected by the separator
- **WHEN** columns `a/z` and `a_b` exist on one side and `a_z` and `a_b` on the other
- **THEN** both sides order them the same way, so identical data gives identical hashes

#### Scenario: Collision within one side
- **WHEN** one side has both `a/b` and `a_b`
- **THEN** the run fails with an error naming both columns

#### Scenario: Exclusion by normalized name
- **WHEN** `--exclude-column order_id` is given and side B has `order/id`
- **THEN** the column is excluded on both sides
