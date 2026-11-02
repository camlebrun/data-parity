## 1. Distribution and CI (spec: pypi-distribution)

- [ ] 1.1 Add `.github/workflows/tests.yml`: ubuntu 3.10 and 3.13, macOS 3.12, Windows 3.10 and 3.13, installing dbt-duckdb, building the DuckDB test bed and running `pytest` with `DBT_CORE` so the real-dbt tests run (written; PR #7 green, waiting for the merge)
- [ ] 1.2 Add `.changie.yaml` (kinds Added/Changed/Fixed/Removed/Security) and `.changes/unreleased/`; generate `CHANGELOG.md` with `changie merge`
- [ ] 1.2b Add a CI check: a fragment is required when `src/` or `dbt_package/` change and its YAML must load; `CHANGELOG.md` must equal `changie merge` output (workflow change: owner's go first)
- [ ] 1.2c Write the changie fragments for the user-visible changes since the project start (batch them into `0.1.0` at release)
- [ ] 1.3 Add `.github/workflows/release.yml`: tag trigger, check tag = `pyproject.toml` version = changelog entry, build sdist and wheel
- [ ] 1.4 Configure PyPI trusted publishing for `data-parity` and publish from the workflow (owner's go first)
- [ ] 1.5 Verify `pipx install data-parity` in a clean environment prints usage and exits 0
- [ ] 1.6 Release 0.1.0 (first public release; checklist in `docs/release-checklist.md`)

## 2. Accuracy benchmark (spec: accuracy-benchmark)

- [ ] 2.1 Convert `integration_tests/expected_findings.md` into a machine-readable ground-truth file in `benchmarks/`
- [ ] 2.2 Write the scorer (found / missed / false positive per case) and make it exit 1 on any miss or false positive
- [ ] 2.3 Run it on the DuckDB fixtures and add a test that fails on the noise-day regression
- [ ] 2.4 Add BigQuery vs Trino runs reusing the existing guards (name prefix, 1-day expiry, no deletion); record versions, date, bytes
- [ ] 2.5 Write `benchmarks/RESULTS.md` with commands, environment, outcomes (including failures) and stated limits
- [ ] 2.6 Add a nightly benchmark workflow (owner's go first; confirm cost stays below 15 EUR per month)

## 3. Engine coverage (spec: engine-coverage)

- [ ] 3.1 Move the 79-row conformance corpus into a data file read by the dbt macro
- [ ] 3.2 Add the engine matrix to the docs with status, date and version per engine
- [ ] 3.3 Add a docs check that fails when an engine is marked `tested in CI` without a job
- [ ] 3.4 Implement PostgreSQL canonical rules and pass conformance against DuckDB
- [ ] 3.5 Implement Snowflake canonical rules and pass conformance (status `tested manually` until a CI job exists)
- [ ] 3.6 Implement Databricks and ClickHouse canonical rules the same way
- [ ] 3.7 Add the float-noise fixture test (1e-12 tolerated, 0.01 reported) for each engine
- [ ] 3.8 Make unsupported types exit 2 naming the column and `exclude_columns` on every engine

## 4. Standalone mode (spec: standalone-mode)

- [ ] 4.1 Define the engine interface in `src/data_parity/engines/` (connect, run grouped scan, run bucket pass, run column pass)
- [ ] 4.2 Implement the DuckDB engine and make `data-parity compare` pass the integration fixtures with the same findings as dbt mode
- [ ] 4.3 Decide the connection-file parser (resolve the YAML Open Question) and implement strict key checking
- [ ] 4.4 Implement lazy-imported optional extras and the missing-driver message with the exact `pip install` command
- [ ] 4.5 Implement the BigQuery and Trino engines and run the BigQuery vs Trino ground truth in standalone mode
- [ ] 4.6 Mask secrets in every message and add a test that scans all outputs for the test password
- [ ] 4.7 Add a test proving dbt mode options, outputs and exit codes are unchanged
- [ ] 4.8 Add a test that the dbt-mode and standalone-mode row hashes are equal for the whole conformance corpus

## 5. Reveal keys (spec: reveal-keys)

- [ ] 5.1 Add a test that greps all default outputs for primary-key values and expects none
- [ ] 5.2 Implement the fourth pass and `--reveal-keys` writing `keys.json` (changed values, `only_in_a`, `only_in_b`)
- [ ] 5.3 Implement `--reveal-limit` with `truncated` and `total`
- [ ] 5.4 Exit 2 with the explanation for tables without a primary key
- [ ] 5.5 Document the privacy trade-off next to the option

## 5b. Reveal values (spec: reveal-values; depends on group 5)

- [ ] 5b.1 Add a test that `parity.md`, `parity.json` and the HTML are identical with and without `--reveal-values`
- [ ] 5b.2 Add the dbt macro that prints, for a differing bucket, `PARITY|` lines with key and row hash, and the macro that prints the values of given keys
- [ ] 5b.3 Implement the value fetch: one query per side, restricted to the differing keys and columns, bounded by `--reveal-limit` (default 20)
- [ ] 5b.4 Implement `--redact COL` at fetch time (the value is never read) and never show columns excluded from the comparison
- [ ] 5b.5 Render the extract as an aligned table (key, column, A, B) with visible trailing spaces and control characters, fitting the terminal width
- [ ] 5b.6 Implement `--reveal-out FILE` with the warning that the file contains data; no other place may hold values
- [ ] 5b.7 Test the manual dbt flow: run the macros on both targets, diff the outputs, find the keys

## 6. Scan cost guard (spec: scan-cost-guard)

- [ ] 6.1 Implement `--dry-run` using BigQuery's dry-run estimate and print per model and side
- [ ] 6.2 Print `estimate unavailable` plus metadata row count for engines without an estimate
- [ ] 6.3 Implement `--max-bytes` exiting 2 before any scan, with the `--last N` suggestion and its estimate
- [ ] 6.4 Record `bytes_estimated` and `bytes_processed` per pass in `parity.json`
- [ ] 6.5 Implement `--price-per-tib` and `--currency` (no built-in price; bytes only without them)
- [ ] 6.6 Make `--dry-run` list every pass the options would run, including the key pass and the value fetch, with upper bounds

## 7. Report formats (spec: report-formats)

- [ ] 7.1 Implement `--html FILE` from the same result object, escaped and with no external resource
- [ ] 7.2 Add tests: offline rendering, same facts as Markdown, hostile column name
- [ ] 7.3 Write the JSON Schema, add `schema_version`, validate fixtures against it in CI
- [ ] 7.4 Add the limited-scope wording to the HTML and verify it in all three formats

## 8. CI integrations (spec: ci-integrations)

- [ ] 8.1 Write the GitHub Action: run, post or update one PR comment, fail on exit 1 or 2
- [ ] 8.2 Test the action against a sample repository (second push updates the comment; exit 2 fails with a clear message)
- [ ] 8.3 Implement the Airflow operator in the `[airflow]` extra with the XCom path and failure on exit 1 or 2
- [ ] 8.4 Check the dbt hub listing requirements (resolve the Open Question) and submit the package with a tag-pinned snippet

## 10. CLI experience (spec: cli-experience) - independent of all other groups, can ship first

- [x] 10.1 Add tests that capture the current non-TTY output of `run`, `conformance` and `report` as golden files
- [x] 10.2 Create `src/data_parity/ui.py` with terminal detection (TTY, `NO_COLOR`, `--no-color`, encoding, width, Windows VT mode)
- [x] 10.3 Implement the verdict card (verdict, engines, scope, duration, one line per model) with ASCII and Unicode variants
- [x] 10.4 Implement live progress on stderr (pass n of 3, current model, skipped passes) without touching stdout
- [x] 10.5 Add the report path and the single next command to the card; add the fix and exact command on errors
- [x] 10.6 Fit the card to the terminal width down to 60 columns (ellipsis, no wrapped rows) with tests at 60, 80 and 120
- [x] 10.7 Rewrite `help` and `--help` (quick-start first, grouped options, one example per command) and add "did you mean" for unknown options
- [x] 10.8 Test that exit codes, `parity.md` and `parity.json` are identical with the old and the new display
- [x] 10.7b Redesign the top-level help as data in `helpview.py`: title and rule, quick start as command + description, examples on their own line, aligned options, wrapped to the width (60 to 100), plain ASCII twin for redirected output
- [ ] 10.7c Give the per-command `--help` (argparse) the same look and an example, keeping the options and defaults
- [ ] 10.7d Iterate on the help with the owner after a trial in a real terminal (grouping, wording, length), then freeze the golden files
- [ ] 10.9 Verify on macOS, Linux and Windows PowerShell terminals and record screenshots for the README
- [ ] 10.10 Trial the CLI on the real DuckDB test bed (`integration_tests/`, dbt-duckdb in an isolated venv) in a terminal: card, progress, help, errors (non-TTY run on the real bed already reproduces the ground truth)

## 9. Documentation and launch (spec: community-launch)

- [ ] 9.1 Rewrite the README in English first, keep the French runbook as a translation
- [ ] 9.2 Link every README claim to its test or benchmark result and label untested ones
- [ ] 9.3 Write the demo script and record the 60-second terminal demo
- [ ] 9.4 Add `CONTRIBUTING.md` (set-up, tests, adding an engine), code of conduct, issue and PR templates
- [ ] 9.5 Open at least five `good first issue` issues
- [ ] 9.6 Write `docs/launch.md` (channels, content, order, success signals) with stars as information only
- [ ] 9.7 Write the metrics script appending date, stars, PyPI downloads and external issues to `docs/metrics.csv`
- [ ] 9.8 Add a release checklist with no star-count condition

## 11. Repository layout (decision: one repository for 0.1.0, see docs/decision-single-repository.md)

- [ ] 11.1 Write `dbt_package/README.md`: the three passes, an example output, what the check proves and does not prove
- [ ] 11.2 Give `dbt_package/` its own version and tests, and link or copy `docs/canonical-spec.md` into it
- [ ] 11.3 Make the CLI declare and check the supported dbt package version range, failing with exit code 2 and the fix
- [ ] 11.4 Re-evaluate splitting into `dbt-data-parity` before any dbt Hub submission (check hub naming conventions first)
