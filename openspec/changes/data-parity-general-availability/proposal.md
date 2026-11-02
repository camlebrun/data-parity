## Why

data-parity proves that a dbt model is identical on two engines without moving data, and a session of tests on real
BigQuery vs Trino fixtures showed it finds exactly the planted differences. But almost nobody can use it today:
it is not on PyPI (`pipx install data-parity` is documented as impossible), it needs a dbt project on both sides, it
covers three engines, it never says which row differs, and nothing public proves it works. The north-star of this
change is **100 000 GitHub stars**. That figure is an aspiration, not a commitment: stars follow adoption, so every
requirement below is a measurable step that removes one adoption blocker, and the star count is only tracked, never
a delivery criterion.

## What Changes

- Publish a versioned package on PyPI with automated, tokenless releases and a changelog.
- Add a **standalone mode** that compares tables through direct connections described in a YAML file or URIs, with
  no dbt project; the dbt mode stays and keeps working unchanged.
- Extend engine coverage beyond BigQuery, DuckDB and Trino (Snowflake, PostgreSQL, Databricks, ClickHouse) with a
  conformance test per engine pair, run in CI.
- Add an opt-in `--reveal-keys` option that lists the primary keys of the rows that differ in a bucket. Default
  behavior (no key, no row value ever printed) is unchanged.
- Add an opt-in `--reveal-values` that shows in the terminal an extract of the differing data (key, column, value on A and on B),
  bounded, with `--redact` for sensitive columns; values never reach the shared reports.
- Keep one repository for `0.1.0` and make `dbt_package/` self-contained so it can be split later
  (see `docs/decision-single-repository.md`).
- Add a `--dry-run` that estimates the scan cost (bytes read on BigQuery, row estimate elsewhere) before any
  scan, and a `--max-bytes` guard.
- Add a GitHub Action that comments the report on a pull request, an Airflow operator, and a listing for the dbt
  package hub.
- Add a self-contained HTML report and a published JSON schema for `parity.json`.
- Add a reproducible, public accuracy benchmark based on the planted-differences ground truth
  (`integration_tests/expected_findings.md`), run on real engines.
- Redesign the terminal output: live progress, a verdict card, a clear next step, optional color and Unicode with
  a plain fallback, and helpful `--help` and error messages. Redirected output stays byte-for-byte as today.
- Rewrite the README and docs in English first (French runbook kept), add a 60-second demo, CONTRIBUTING, issue
  templates, and a launch plan with a star-count tracking script.
- **BREAKING**: none. Every addition is opt-in; exit codes (0 identical, 1 differences, 2 error) and the content of
  `parity.md` / `parity.json` stay as they are, only new optional fields are added.

## Capabilities

### New Capabilities
- `pypi-distribution`: installable from PyPI, semantic versions, tokenless release pipeline, changelog, OS matrix in CI.
- `standalone-mode`: compare two tables from a YAML/URI description with native drivers, without dbt.
- `engine-coverage`: supported-engine matrix, a portable-hash conformance check per engine pair, CI proof.
- `reveal-keys`: opt-in listing of the keys of differing rows, bounded and local.
- `reveal-values`: opt-in terminal extract of the differing data (key, column, A and B values), bounded and redactable.
- `scan-cost-guard`: dry-run estimate (optionally in money) and byte ceiling before any scan, covering every pass.
- `ci-integrations`: GitHub Action PR comment, Airflow operator, dbt hub listing.
- `report-formats`: self-contained HTML report and published JSON schema.
- `accuracy-benchmark`: reproducible ground-truth benchmark and its published results.
- `community-launch`: English-first docs, demo, contribution guide, launch plan, star-count tracking.
- `cli-experience`: live progress, verdict card, next-step hints, color and Unicode with fallbacks, helpful help and errors.

### Modified Capabilities
<!-- None: openspec/specs/ is empty, nothing is modified. -->

## Impact

- Code: `src/data_parity/` (new modules for connectors, cost estimation, key reveal, HTML report), `dbt_package/`
  (unchanged in dbt mode; hash macros ported for new engines), `pyproject.toml` (extras per engine, entry point).
- CI/CD: new GitHub Actions workflows (tests matrix, PyPI release, benchmark). Workflow changes need the owner's go
  before being applied, one or two lines of explanation each.
- Docs: README (EN), `docs/`, `CONTRIBUTING.md`, issue templates.
- Dependencies: the core stays standard-library only; each engine driver is an optional extra
  (`data-parity[bigquery]`, `[snowflake]`, ...).
- Cost: cloud benchmark runs read small fixtures (< 1 GB per run on BigQuery, about 0 EUR); any run above 15 EUR
  per month needs a separate decision.
- Non-goals: comparison with other tools as a delivery criterion, a hosted SaaS, row-level data export by default,
  fuzzy matching of values.
