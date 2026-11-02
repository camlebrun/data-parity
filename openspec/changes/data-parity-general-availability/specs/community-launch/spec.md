## ADDED Requirements

### Requirement: English-first documentation
The README and the docs SHALL be written in English first, with the French runbook kept as a translation, and the
README SHALL let a new user run a first comparison in under 5 minutes on the bundled fixtures.

#### Scenario: Quick start works as written
- **WHEN** the quick-start commands are run in a clean environment
- **THEN** they reproduce the documented result with exit code 1 and the five documented differing days

### Requirement: Demo
The README SHALL embed a demo of at most 60 seconds (terminal recording) showing install, a run with differences,
and the report, and the recording SHALL be regenerable by a script.

#### Scenario: Regeneration
- **WHEN** the demo script is run
- **THEN** it produces the recording from the current version of the tool

### Requirement: Contribution path
The repository SHALL provide `CONTRIBUTING.md` (set-up, tests, how to add an engine), a code of conduct, issue and
pull-request templates, and a `good first issue` label applied to at least five open issues.

#### Scenario: Adding an engine
- **WHEN** a contributor follows the guide to add an engine
- **THEN** the guide lists the files to add and the conformance command that must pass

### Requirement: Honest claims
Every claim in the README (supported engines, platforms, performance, privacy) SHALL be backed by a test or a
benchmark result linked next to it, and untested claims SHALL be labelled as such.

#### Scenario: Unproven platform
- **WHEN** a platform has no CI job
- **THEN** the README does not list it as supported

### Requirement: Launch plan
The repository SHALL contain `docs/launch.md` with the channels, the content for each, the order, and the success
signals (installs from PyPI, issues opened by others, external pull requests), with GitHub stars recorded as an
informational metric only.

#### Scenario: Metric tracking
- **WHEN** the tracking script runs
- **THEN** it appends the date, the star count, the PyPI download count and the count of externally opened issues
  to `docs/metrics.csv`

### Requirement: Milestones are not promises
Documentation SHALL describe the 100 000-star goal as an aspiration and SHALL NOT present any star count as a
delivery criterion of any release.

#### Scenario: Release checklist
- **WHEN** the release checklist is read
- **THEN** it contains no star-count condition
