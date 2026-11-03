## ADDED Requirements

### Requirement: Readable live progress
On an interactive terminal, the CLI SHALL show the run as it happens: the current pass (1 of 3, 2 of 3, 3 of 3),
the model being scanned, and an animated indicator, written to stderr so that stdout stays clean.

#### Scenario: Interactive run
- **WHEN** `data-parity run` is started in a terminal
- **THEN** the current pass and model are shown and updated in place, and nothing from the animation reaches stdout

#### Scenario: Pass skipped
- **WHEN** pass 1 finds every model identical
- **THEN** the display shows passes 2 and 3 as skipped, not as pending

### Requirement: Verdict card
At the end of a run the CLI SHALL print one summary card: a verdict line (identical, differences, error, or
identical on a limited scope), the engines and targets compared, the scope, the duration, and one line per model with
its status.

#### Scenario: Differences found
- **WHEN** a run finds differences in two of three models
- **THEN** the card shows the verdict `DIFFERENCES`, the two differing models first with their differing partitions
  and columns, and the identical model last

#### Scenario: Limited scope
- **WHEN** a run uses `--last 30` and finds no difference
- **THEN** the card says `identical on the checked scope` and states the scope, never a plain `ISO`

### Requirement: Next step is always shown
The card SHALL end with the path of the report and the single most useful next command (for example the command to
re-render the report, or how to reveal keys), and on an error it SHALL give the fix and the exact command to run.

#### Scenario: Error
- **WHEN** a dbt call fails on side B because the package is missing
- **THEN** the card names side B, the cause, and prints `dbt deps` as the command to run

### Requirement: Color and Unicode are optional, never required
The CLI SHALL use color and box-drawing characters only when stdout is a terminal that supports them, SHALL honor
the `NO_COLOR` environment variable and a `--no-color` option, SHALL fall back to ASCII when the output encoding
cannot represent the characters, and SHALL work on Windows PowerShell, macOS and Linux terminals.

#### Scenario: NO_COLOR set
- **WHEN** `NO_COLOR=1` is set
- **THEN** no escape sequence is written

#### Scenario: Limited encoding
- **WHEN** the output encoding is not UTF-8
- **THEN** the card is drawn with ASCII characters and stays aligned

### Requirement: Piped output stays exactly as before
When stdout is not a terminal, the CLI SHALL print the current plain-text output, byte for byte, with no animation, no
color and no box drawing, so scripts and the existing tests keep working.

#### Scenario: Redirected output
- **WHEN** the output of `data-parity run` is redirected to a file
- **THEN** the file contains the same lines as the previous version and no escape sequence

### Requirement: Adapts to the terminal width
The card SHALL fit the terminal width, truncating long names with an ellipsis and never wrapping a table row, down to
a width of 60 columns.

#### Scenario: Narrow terminal
- **WHEN** the terminal is 60 columns wide and a model name is 80 characters long
- **THEN** the name is truncated with an ellipsis and every row fits on one line

### Requirement: Helpful help and errors
`data-parity help` and `--help` SHALL start with a short quick-start (the three commands to run first), group options
by purpose, and show an example per command; a usage error SHALL name the bad option and suggest the closest valid
one.

#### Scenario: Misspelled option
- **WHEN** the user types `--schema-aa`
- **THEN** the error says the option is unknown and suggests `--schema-a`

### Requirement: A help that is pleasant to read
On an interactive terminal, `data-parity help` SHALL be laid out for reading, not dumped: a title line with the version,
sober section titles with a rule, each quick-start command on its own line with its description underneath, each
example on its own line, options aligned in columns, and every line wrapped to the terminal width (down to 60 columns)
without cutting a word. Redirected help SHALL carry the same content as plain ASCII text at a fixed width, with no escape
sequence. Both renderings SHALL come from one description of the content, so they cannot drift apart.

#### Scenario: Narrow terminal
- **WHEN** `data-parity help` is shown in a 60-column terminal
- **THEN** no line is longer than 60 columns and every option of the wide rendering is still listed

#### Scenario: Redirected help
- **WHEN** the help is piped to a file
- **THEN** it holds no escape sequence and no non-ASCII character, and lists the same options as the terminal rendering

#### Scenario: Examples are readable
- **WHEN** a command has an example
- **THEN** the example is on its own line under the description, introduced by `e.g.`, so the command to copy is never split inside a sentence

### Requirement: Report path and next command stay copyable
The report path and the next command SHALL be printed under the card frame, never truncated, because a cut command cannot be pasted.

#### Scenario: Long run directory
- **WHEN** the run directory path is longer than the card width
- **THEN** the frame keeps its width and the `report:` and `next:` lines are printed in full below it

### Requirement: Exit codes and files unchanged
Display changes SHALL NOT alter exit codes (0 identical, 1 differences, 2 error), the content of `parity.md`, or
the content of `parity.json`.

#### Scenario: Same result, new display
- **WHEN** the same run is made with the old and the new display
- **THEN** exit code, `parity.md` and `parity.json` are identical
