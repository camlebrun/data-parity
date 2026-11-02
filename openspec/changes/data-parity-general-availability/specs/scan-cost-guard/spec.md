## ADDED Requirements

### Requirement: Dry-run estimate
`--dry-run` SHALL print, for each model and side, the estimated scan size before any hashing query is executed, and
SHALL exit 0 without reading table data. On BigQuery the estimate SHALL come from the engine's dry-run mechanism.

#### Scenario: BigQuery estimate
- **WHEN** `data-parity run --dry-run` is used on a BigQuery side
- **THEN** it prints the bytes the first pass would read, and no billable query is run

#### Scenario: Engine without estimate
- **WHEN** an engine offers no byte estimate
- **THEN** the output states `estimate unavailable` and gives the row count from table metadata when known

### Requirement: Byte ceiling
`--max-bytes N` SHALL stop the run with exit code 2 before any scan when the estimate exceeds N, naming the model
and the estimate.

#### Scenario: Ceiling exceeded
- **WHEN** the estimate for `fct_orders` is 5 GB and `--max-bytes 1GB` is set
- **THEN** the run exits 2 before any scan and names `fct_orders`

### Requirement: Scope reduction is suggested
When a ceiling or estimate is large, the message SHALL suggest `--last N` and `--as-of` and show the estimate they
would give.

#### Scenario: Suggestion
- **WHEN** the ceiling is exceeded and a partition column exists
- **THEN** the message includes the estimate with `--last 30`

### Requirement: Cost shown in money when a price is given
When `--price-per-tib AMOUNT` (and optionally `--currency CODE`) is given, the dry-run SHALL print the estimated cost next to the
bytes. The tool SHALL NOT ship a built-in price, because prices change; without the option it prints bytes only.

#### Scenario: Price given
- **WHEN** the estimate is 0.5 TiB and `--price-per-tib 6.25 --currency USD` is given
- **THEN** the dry-run prints `3.13 USD` next to the byte count

#### Scenario: No price
- **WHEN** `--price-per-tib` is not given
- **THEN** the dry-run prints bytes only and no amount of money

### Requirement: Dry-run covers every pass, including reveal
`--dry-run` SHALL list the estimate of each pass that the chosen options would run, including the key pass (`--reveal-keys`) and the
value fetch (`--reveal-values`), with the upper bound for the passes whose size depends on the result of an earlier pass.

#### Scenario: Reveal options
- **WHEN** `--dry-run --reveal-values` is used
- **THEN** the output lists passes 1 to 3, the key pass and the value fetch, each with its estimate or an upper bound

### Requirement: Estimate in the report
The report SHALL record the estimated and, when the engine returns it, the actual bytes read per pass.

#### Scenario: Recorded cost
- **WHEN** a BigQuery run completes
- **THEN** `parity.json` holds `bytes_estimated` and `bytes_processed` for each pass
