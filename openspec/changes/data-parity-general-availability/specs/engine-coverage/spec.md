## ADDED Requirements

### Requirement: Declared engine matrix
The documentation SHALL list every supported engine with a status (`tested in CI`, `tested manually`, `untested`)
and the date and version of the last test, and an engine SHALL NOT be marked `tested in CI` without a passing
job.

#### Scenario: Claim without proof
- **WHEN** an engine appears as `tested in CI` and no CI job covers it
- **THEN** the documentation check fails

### Requirement: Engines added
The tool SHALL support, in addition to BigQuery, DuckDB and Trino: Snowflake, PostgreSQL, Databricks and ClickHouse,
each with a canonical-text implementation for every supported column type.

#### Scenario: Supported type on a new engine
- **WHEN** a `DECIMAL(18,2)` value is hashed on PostgreSQL and on BigQuery
- **THEN** both engines produce the same row hash

### Requirement: Conformance per engine pair
`data-parity conformance` SHALL run for every pair of supported engines available in CI, and SHALL exit 1 and name
the first diverging type when a hash is not portable.

#### Scenario: Divergence detected
- **WHEN** a canonical rule is changed on one engine only
- **THEN** conformance exits 1 naming the type and a sample value

### Requirement: Unsupported types fail loudly
A column type without a canonical rule SHALL stop the run with an exit code 2 and the exclusion option to use, and
SHALL NOT be skipped silently.

#### Scenario: Array column
- **WHEN** a model has an array column and no exclusion
- **THEN** the run exits 2 naming the column and `exclude_columns`

### Requirement: Float noise tolerance documented
The canonical rule for floating-point values SHALL be documented with its scale and a fixture proving that a
relative difference of 1e-12 is tolerated while a difference of 0.01 is reported.

#### Scenario: Noise day
- **WHEN** one day differs only by 1e-12 relative noise on a float column
- **THEN** that day is reported as identical
