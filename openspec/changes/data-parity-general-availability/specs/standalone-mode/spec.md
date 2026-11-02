## ADDED Requirements

### Requirement: Compare without a dbt project
The CLI SHALL provide `data-parity compare` which compares one table on side A with one table on side B using a
connection description (URI or YAML file) and no dbt project, using the same hash, drill-down passes, report and
exit codes as the dbt mode.

#### Scenario: Identical tables
- **WHEN** `data-parity compare --a duckdb://a.duckdb --b duckdb://b.duckdb --table mart.orders` is run on identical tables
- **THEN** it prints the verdict `ISO`, writes `parity.md` and `parity.json`, and exits 0

#### Scenario: Differing tables
- **WHEN** the two tables differ in one partition
- **THEN** the report names the partition, the bucket and the differing column, and the exit code is 1

#### Scenario: Connection failure
- **WHEN** a side cannot be reached
- **THEN** the command exits 2 naming the side and the cause, never 0 or 1

### Requirement: Connection file
The CLI SHALL read a YAML file with one block per side (engine, connection settings, table, key columns,
partition column, excluded columns) and SHALL reject unknown keys.

#### Scenario: Unknown key
- **WHEN** the file contains a misspelled key such as `partion_column`
- **THEN** the command exits 2 and names the key and the line

### Requirement: Secrets are never stored or printed
Credentials SHALL come from environment variables or the engine's own credential chain, SHALL NOT be written to
`parity.md`, `parity.json` or logs, and a password given inside a URI SHALL be masked wherever the URI is shown.

#### Scenario: Masked URI in an error
- **WHEN** a connection with `user:secret@host` fails
- **THEN** the error message shows `user:***@host`

### Requirement: Drivers are optional extras
Each engine driver SHALL be an optional extra (`data-parity[bigquery]`, `[snowflake]`, ...), and using an engine
whose extra is missing SHALL exit 2 with the exact `pip install` command to run.

#### Scenario: Missing driver
- **WHEN** a Snowflake side is used and the extra is not installed
- **THEN** the command exits 2 and prints `pip install "data-parity[snowflake]"`

### Requirement: dbt mode unchanged
All existing `data-parity run` and `conformance` options, outputs and exit codes SHALL keep working unchanged.

#### Scenario: Existing fixtures
- **WHEN** `data-parity run --a engine_a --b engine_b` is run on the integration fixtures
- **THEN** it reports exactly the findings of `expected_findings.md` and exits 1
