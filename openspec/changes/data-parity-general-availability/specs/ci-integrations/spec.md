## ADDED Requirements

### Requirement: GitHub Action
The repository SHALL provide a reusable GitHub Action that runs `data-parity` and posts the Markdown report as a
single pull-request comment, updating that same comment on later pushes, and that fails the job when the exit code
is 1 or 2.

#### Scenario: Differences on a pull request
- **WHEN** the action runs on a pull request and the tool exits 1
- **THEN** one comment containing the report is posted and the job fails

#### Scenario: Second push
- **WHEN** the action runs again on the same pull request
- **THEN** the existing comment is updated, no second comment is created

#### Scenario: Error is not a pass
- **WHEN** the tool exits 2
- **THEN** the job fails and the comment states that the check could not run

### Requirement: Report contains no row data
The comment SHALL contain only what `parity.md` contains, and SHALL NOT include `keys.json`.

#### Scenario: Reveal keys enabled
- **WHEN** the run used `--reveal-keys`
- **THEN** the comment still contains no key

### Requirement: Airflow operator
The package SHALL provide an Airflow operator, in an optional extra `data-parity[airflow]`, that runs a comparison
and fails the task on exit code 1 or 2, pushing the path of `parity.json` to XCom.

#### Scenario: Differences fail the task
- **WHEN** the operator's comparison exits 1
- **THEN** the task ends in `failed` and the XCom holds the path of `parity.json`

#### Scenario: Core import stays light
- **WHEN** `data_parity` is imported without Airflow installed
- **THEN** the import succeeds

### Requirement: dbt package hub listing
The dbt package SHALL be published on the dbt package hub with a pinned version and an install snippet in the README
that uses a tag, never a branch.

#### Scenario: Install snippet
- **WHEN** a user copies the snippet into `packages.yml` and runs `dbt deps`
- **THEN** the package installs at the tagged version
