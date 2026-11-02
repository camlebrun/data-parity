## ADDED Requirements

### Requirement: Opt-in extract of the differing data
With `--reveal-values`, the CLI SHALL show in the terminal an extract of the rows that differ: for each row the key, the
differing column, and the value on side A and on side B. It depends on `--reveal-keys`: using `--reveal-values` implies the key
listing. Without the option, no value SHALL be read or printed.

#### Scenario: Changed value
- **WHEN** `--reveal-values` is used and order 20 has `amount` 10.00 on A and 10.01 on B
- **THEN** the terminal shows a row with key `20`, column `amount`, A `10.00`, B `10.01`

#### Scenario: Row missing on one side
- **WHEN** order 10 exists only on side A
- **THEN** the terminal shows key `10`, marked as only in A, with the values of the compared columns from A

#### Scenario: Default run
- **WHEN** a run finds differences without `--reveal-values`
- **THEN** no cell value of any table appears in the console, the files or the logs

### Requirement: Only the differing rows are read
Values SHALL be fetched only for the keys already known to differ, by a query restricted to those keys and to the differing
columns, bounded by the row limit; the tool SHALL NOT read whole tables to display values.

#### Scenario: Bounded read
- **WHEN** 5,000 rows differ and the limit is 20
- **THEN** values are fetched for at most 20 keys on each side

### Requirement: Bounded and sorted extract
The extract SHALL show at most `--reveal-limit N` rows (default 20), ordered by partition then key, and SHALL state the total number
of differing rows and that the display is truncated when it is.

#### Scenario: Truncation
- **WHEN** 5,000 rows differ and the default limit applies
- **THEN** 20 rows are shown with the line `showing 20 of 5,000 differing rows`

### Requirement: Sensitive columns can be hidden
`--redact COL` (repeatable) SHALL replace the values of that column by `***` in the display while still reporting that the column
differs, and values of columns excluded from the comparison SHALL never be shown.

#### Scenario: Redacted column
- **WHEN** `--reveal-values --redact email` is used and `email` differs
- **THEN** the row shows column `email` with `***` on both sides

### Requirement: Values stay on the user's machine and out of shared artifacts
Values SHALL be written only to the terminal by default; they SHALL NOT appear in `parity.md`, `parity.json`, the HTML report, the
GitHub Action comment or logs. A file with values SHALL be written only when `--reveal-out FILE` is given, and the tool SHALL warn
that this file contains data.

#### Scenario: Shared report
- **WHEN** a run uses `--reveal-values`
- **THEN** `parity.md` and `parity.json` are identical to those of the same run without it

#### Scenario: Explicit output file
- **WHEN** `--reveal-out diff.json` is given
- **THEN** the file holds the same extract and the terminal prints a warning that it contains data

### Requirement: Readable rendering
On an interactive terminal the extract SHALL be rendered as an aligned table (key, column, A, B) that fits the terminal width,
truncating long values with an ellipsis and showing control characters and trailing spaces visibly (for example `'paid '`), so that a
whitespace-only difference can be seen. Piped output SHALL be plain text.

#### Scenario: Trailing space
- **WHEN** the status is `paid` on A and `paid ` on B
- **THEN** the table shows `'paid'` and `'paid '` so the difference is visible

### Requirement: Works from the dbt package alone
The dbt package SHALL expose the macros needed to print the differing keys, and then the values of those keys, as `PARITY|` lines, so
a user without the Python CLI can diff the two outputs by hand.

#### Scenario: Manual dbt flow
- **WHEN** the macro is run on both targets for a differing bucket and the two outputs are diffed
- **THEN** the diff shows the keys present on one side only and the keys whose row hash differs
