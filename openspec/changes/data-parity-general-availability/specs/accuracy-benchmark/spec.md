## ADDED Requirements

### Requirement: Ground-truth benchmark
The repository SHALL contain a `benchmarks/` suite that generates deterministic fixtures with planted differences,
runs data-parity on them, and compares the result with a machine-readable ground truth, reporting per planted case
whether it was found (recall) and per unplanted case whether it was flagged (false positives).

#### Scenario: Perfect run
- **WHEN** the benchmark runs on the DuckDB fixtures
- **THEN** it reports 5 of 5 planted `orders` days found, 0 false positives, and exits 0

#### Scenario: Regression
- **WHEN** a change makes the noise day (1e-12 float difference) flagged
- **THEN** the benchmark reports one false positive and exits 1

### Requirement: Real-engine runs
The benchmark SHALL support running against BigQuery and Trino with the same ground truth, and SHALL record the
engine versions, the tool version, the date and the bytes read.

#### Scenario: Recorded environment
- **WHEN** a BigQuery versus Trino run completes
- **THEN** the result file holds the tool version, the engine versions, the date and `bytes_processed`

### Requirement: Cost-bounded cloud use
Cloud runs SHALL use only the generated fixtures, create no dataset without the owner's explicit approval, delete
nothing, and use datasets whose tables expire within one day.

#### Scenario: Dataset already exists
- **WHEN** the target dataset exists
- **THEN** the benchmark stops and asks, and neither drops nor overwrites anything

### Requirement: Published, reproducible results
`benchmarks/RESULTS.md` SHALL list each run with its command, environment and outcome, including failures, and
SHALL state the limits of the test (fixture size, engines, what was not measured).

#### Scenario: Reproduction
- **WHEN** a reader runs the command listed for a result on the same fixtures
- **THEN** they obtain the same counts of found and false-positive cases

### Requirement: No dependency on another tool
The benchmark SHALL measure data-parity against the ground truth only. Comparisons with other tools MAY be added
as separate, clearly labelled documents, and SHALL NOT be required for the benchmark to pass.

#### Scenario: Only data-parity installed
- **WHEN** no other comparison tool is installed
- **THEN** the benchmark runs and passes or fails on the ground truth alone
