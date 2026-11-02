## ADDED Requirements

### Requirement: Keys are hidden by default
Without `--reveal-keys`, no primary-key value and no row value SHALL appear in the console, `parity.md`,
`parity.json` or logs.

#### Scenario: Default run
- **WHEN** a run finds differences without `--reveal-keys`
- **THEN** the outputs contain only counts, hash sums, partition values, bucket numbers and column names

### Requirement: Opt-in key listing
With `--reveal-keys`, the tool SHALL list the primary keys of the rows that differ inside each differing bucket,
in a separate file `keys.json` that is not embedded in `parity.md`, and SHALL tell the user where the file is.

#### Scenario: Changed values
- **WHEN** `--reveal-keys` is used and order 20 and 140 differ on `amount`
- **THEN** `keys.json` lists `20` and `140` with the partition and the differing column, and no column value

#### Scenario: Missing and extra rows
- **WHEN** rows exist on only one side
- **THEN** `keys.json` lists their keys under `only_in_a` or `only_in_b`

### Requirement: Bounded output
The listing SHALL be capped by `--reveal-limit N` (default 1000 keys per model); above the cap the file SHALL state
the total and that the list is truncated.

#### Scenario: Cap reached
- **WHEN** 5000 rows differ and the default cap applies
- **THEN** `keys.json` holds 1000 keys and `truncated: true` with `total: 5000`

### Requirement: Keys stay inside the user's environment
The tool SHALL read keys through the engines' own connections and SHALL NOT send them to any external service.

#### Scenario: No network beyond the engines
- **WHEN** `--reveal-keys` runs
- **THEN** the only network connections are to the configured engines

### Requirement: No primary key
For a model without a primary key, `--reveal-keys` SHALL exit 2 explaining that keys are unavailable and naming
the `primary_key` setting.

#### Scenario: Table without key
- **WHEN** `--reveal-keys` is used on `events_nopk`
- **THEN** the run exits 2 with the explanation
