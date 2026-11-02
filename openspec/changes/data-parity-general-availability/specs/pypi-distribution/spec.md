## ADDED Requirements

### Requirement: Installable from PyPI
The package SHALL be published on PyPI under the name `data-parity`, and `pip install data-parity` and
`pipx install data-parity` SHALL provide the `data-parity` command with the same behavior as `python -m data_parity`.

#### Scenario: Install in a clean environment
- **WHEN** a user runs `pipx install data-parity` in an environment with only Python 3.9 or newer
- **THEN** `data-parity help` exits 0 and prints the usage

#### Scenario: Core has no third-party dependency
- **WHEN** the package is installed without extras
- **THEN** `pip show data-parity` lists no required dependency

### Requirement: Tokenless, reproducible releases
A release SHALL be created only from a version tag, built and published by a CI workflow using PyPI trusted
publishing (no long-lived token stored), and the tag, the package version and the changelog entry SHALL match.

#### Scenario: Tag and version mismatch
- **WHEN** a tag `v1.2.0` is pushed while `pyproject.toml` declares `1.1.0`
- **THEN** the release workflow fails before any upload

#### Scenario: Successful release
- **WHEN** a tag `v1.2.0` is pushed and the version and changelog both read `1.2.0`
- **THEN** the sdist and wheel are uploaded and a GitHub release is created with the changelog entry

### Requirement: Versioned changelog
Every release SHALL have a dated changelog entry listing user-visible changes, and breaking changes SHALL be marked
`BREAKING`.

#### Scenario: Release without entry
- **WHEN** the release workflow runs and the changelog has no entry for the tag
- **THEN** the workflow fails with a message naming the missing entry

### Requirement: Changelog built from changie fragments
The repository SHALL use changie: a `.changie.yaml` configuration, one fragment file per user-visible change under
`.changes/unreleased/`, and `CHANGELOG.md` generated from those fragments at release time (never edited by hand).
Each fragment SHALL be valid YAML and SHALL declare a kind (for example Added, Changed, Fixed, Removed, Security) and a body;
a breaking change SHALL be marked `BREAKING` in the body.

#### Scenario: Fragment for a user-visible change
- **WHEN** a pull request changes the CLI behavior, an option, an output format, or the dbt package
- **THEN** it contains a changie fragment whose YAML loads without error, and the CI check fails if it is missing

#### Scenario: Documentation-only change
- **WHEN** a pull request changes only documentation, specs or tests
- **THEN** no fragment is required and the CI check passes

#### Scenario: Release batches the fragments
- **WHEN** the release `0.1.0` is prepared
- **THEN** `changie batch 0.1.0` moves the unreleased fragments into the `0.1.0` entry and `changie merge` regenerates `CHANGELOG.md`

#### Scenario: Hand-edited changelog
- **WHEN** `CHANGELOG.md` differs from the output of `changie merge`
- **THEN** the CI check fails

### Requirement: Tested on three operating systems
The test suite SHALL run in CI on Linux, macOS and Windows for each supported Python version, and a failing
combination SHALL block the release.

#### Scenario: Windows failure blocks release
- **WHEN** the test job fails on Windows
- **THEN** the release workflow does not publish
