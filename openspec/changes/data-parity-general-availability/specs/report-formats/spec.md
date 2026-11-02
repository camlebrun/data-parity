## ADDED Requirements

### Requirement: Self-contained HTML report
`--html FILE` SHALL write a single HTML file with no external resource (no network font, script or image), showing
the same content as `parity.md`: verdict, scope, per-model diagnosis, differing partitions and columns, what is not
checked, and the reproduction command.

#### Scenario: Offline rendering
- **WHEN** the HTML file is opened without network access
- **THEN** it renders completely

#### Scenario: Same facts as Markdown
- **WHEN** both reports are produced for one run
- **THEN** every count, partition and column in the HTML appears identically in the Markdown

### Requirement: Escaping
Every value inserted in the HTML (model names, column names, partition values) SHALL be escaped.

#### Scenario: Hostile column name
- **WHEN** a column is named `<script>alert(1)</script>`
- **THEN** the HTML shows it as text and contains no executable script

### Requirement: Published JSON schema
The repository SHALL publish a JSON Schema for `parity.json`, versioned with the package, and every produced
`parity.json` SHALL carry a `schema_version` field and validate against that schema.

#### Scenario: Validation
- **WHEN** a `parity.json` produced by the integration fixtures is validated against the schema
- **THEN** validation passes

#### Scenario: Additive evolution
- **WHEN** a new optional field is added in a minor version
- **THEN** documents of the previous minor version still validate

### Requirement: Partial scope stays explicit
Every format SHALL state `ISO on the checked scope` when `--last`, `--as-of` or sampling limits the check, and
SHALL never show a plain `ISO` verdict in that case.

#### Scenario: Last N
- **WHEN** a run uses `--last 30` and finds no difference
- **THEN** the HTML, the Markdown and the JSON all state a limited scope
