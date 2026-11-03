## Context

Current state (read from the repository):

- `src/data_parity/` is about 2 000 lines of standard-library Python: `cli.py` (options), `runner.py` (calls dbt),
  `drill.py` (three passes: partition, key bucket, column), `report.py` (Markdown and JSON), `mapping.py`,
  `envfile.py`.
- All SQL lives in the dbt package (`dbt_package/macros/`: `canonical.sql`, `row_hash.sql`, `reconcile.sql`,
  `cache.sql`, `conformance.sql`, `portable.sql`). The Python never opens a warehouse connection: it runs
  `dbt run-operation` and parses `PARITY|` lines. This is why dbt is mandatory today.
- `pyproject.toml`: version 0.1.0, MIT, `dependencies = []`, Python >= 3.10 (was 3.9, raised in PR #7). There is no `.github/workflows`
  directory: the cross-platform claim in the README has no CI proof, and nothing is published on PyPI.
- A test session on real BigQuery vs Trino fixtures reproduced the ground truth exactly (5 differing days on
  `fct_orders`, column `city`, day 2025-01-06). That ground truth is the regression oracle for everything below.

Constraints: the core must stay dependency-free; privacy by default (no key, no row value printed); exit codes
0/1/2 and the content of `parity.md` / `parity.json` must not change; the owner is cost-sensitive (flag anything
above 15 EUR per month); CI/CD changes need the owner's go.

## Goals / Non-Goals

**Goals:**
- Remove the five adoption blockers: not installable, dbt required, three engines, no row identification, no
  public proof.
- Keep one definition of the row hash across dbt mode and standalone mode, proven equal by tests.
- Make every claim in the docs backed by a CI job or a benchmark result.

**Non-Goals:**
- A hosted service, a UI beyond the HTML report, or fuzzy value matching.
- Making a star count a delivery criterion (100 000 stars is an aspiration tracked as information).
- Comparison with other tools as a gate; the benchmark measures data-parity against its own ground truth.
- Replacing the dbt mode.

## Decisions

### D1. Standalone mode generates SQL in Python from the canonical spec
Standalone mode needs SQL without Jinja. Decision: add `src/data_parity/engines/` with one small class per engine
(connection, dialect snippets for canonical text, hash, grouped scan) built from `docs/canonical-spec.md`.
- Alternative: render the dbt macros without dbt. Rejected: the macros use `adapter.dispatch` and Jinja context
  that cannot be reproduced reliably.
- Alternative: make dbt a hard dependency of standalone mode. Rejected: it defeats the point.
- Drift risk between the macros and the Python dialects is handled by D2.

### D2. One conformance corpus decides who is right
The 79-row synthetic conformance corpus becomes data (a file), consumed by the dbt macro and by every Python
engine class. CI runs it for every available engine pair in both modes, so a rule changed on one side fails the
build. The canonical spec remains the single human-readable reference.

### D3. Drivers are optional extras with lazy imports
`pip install data-parity` stays dependency-free. `data-parity[bigquery]`, `[snowflake]`, `[postgres]`,
`[databricks]`, `[clickhouse]`, `[duckdb]`, `[trino]` pull one driver each, imported only when that engine is used;
a missing driver exits 2 with the exact install command. Alternative (one fat install) rejected: slow install,
version conflicts.

### D4. Connection description is a strict YAML file, URIs are sugar
YAML (with unknown-key rejection) is the canonical form because it carries keys, partitions and exclusions; a URI
covers the simple case. YAML needs a parser, so it is read with a minimal built-in subset reader for the flat
structure used here, or via the optional `pyyaml` extra for the full syntax (to be decided, see Open Questions).
Secrets come only from environment variables or each engine's credential chain and are masked in every message.

### D5. `--reveal-keys` is a separate artifact and a separate pass
A fourth pass runs only on buckets already known to differ and writes `keys.json` apart from the report. The
default outputs stay key-free by construction (not by filtering), which keeps the privacy claim testable: a test
greps all default outputs for key values.

### D6. Cost guard uses the engine's own estimate
BigQuery exposes a dry-run byte estimate; other engines expose row estimates from metadata or none. The guard
reports `estimate unavailable` rather than guessing. `--max-bytes` applies only where an estimate exists and the
message says when it cannot be enforced.

### D7. Releases through trusted publishing, driven by tags
A tag-triggered workflow builds sdist and wheel and uploads with PyPI trusted publishing (no stored token). The
workflow refuses when tag, package version and changelog disagree. A tests matrix (Linux, macOS, Windows; Python
3.10 to 3.13, with a real dbt-duckdb run of integration_tests/) gates the release. Changie fragments (already used by the owner) produce the changelog.

### D8. The benchmark is ground truth first
`benchmarks/` generalizes `integration_tests/generate.py` and `expected_findings.md` into a machine-readable truth
file. The runner scores found/missed/false-positive per case and writes `RESULTS.md` with environment and bytes
read. Cloud runs reuse the existing guards (dataset names starting with `mart_parity_test`, 1-day expiry, nothing
deleted without approval). Any comparison with another tool lives in a separate document, never in the pass/fail.

### D9. HTML report is a static render of the same data
The HTML is produced from the same in-memory result as the Markdown and JSON, with all values escaped and no
external resource. Alternative (a JavaScript viewer) rejected: needs a build step and breaks the offline rule.

### D10. A terminal renderer built on the standard library
The "wow" comes from structure, not from a dependency: a small `ui.py` module draws the live progress (stderr) and the
verdict card (stdout) with ANSI sequences and box-drawing characters, using `shutil.get_terminal_size`, `sys.stdout.isatty()`,
`NO_COLOR` and the output encoding to choose between rich, ASCII and plain rendering. On Windows it enables virtual
terminal processing. The result object produced by `drill.py` is rendered by `ui.py`; `report.py` is untouched, so
`parity.md` and `parity.json` cannot change.
- Alternative: depend on `rich`. Rejected: it breaks the "no dependency" promise of the core. It MAY be offered later as
  an optional extra without changing the default.
- Non-TTY output keeps the current text byte for byte (existing tests in `tests/test_cli.py` guard it), so the redesign
  is a TTY-only layer.

### D10b. Help is data drawn twice
The top-level help is described once in `helpview.py` (sections, rows, examples) and drawn by one function: rich on a terminal
(titles, rule, colors, wrapping to the width) and plain ASCII at a fixed width otherwise. Per-command `--help` still comes from
argparse; aligning it with the same look is a follow-up (task 10.7c). The report path and next command sit under the card frame,
unframed and uncut, because a truncated command cannot be copied. The golden files of the help are regenerated on purpose
(`UPDATE_GOLDEN=1`) when the content changes: the help is the one plain output allowed to change.

### D11. One repository for 0.1.0, dbt package self-contained
dbt Hub needs `dbt_project.yml` at the repository root, so `dbt_package/` in a subdirectory is not listable. Decision: stay in one
repository (single source of truth for the hash, one conformance suite, one release), and give `dbt_package/` its own README, tests and
version so a later split into `dbt-data-parity` is mechanical. If split, the dbt repository becomes the source of truth for the macros and
the canonical spec, and the Python package pins and checks a version of it. Full record: `docs/decision-single-repository.md`.

### D12. Showing the differing data is a separate, explicit step
`--reveal-values` runs after `--reveal-keys`: the key pass finds which keys differ (key and row hash only), then one small query per side
fetches the compared columns for those keys only, bounded by `--reveal-limit`. Values go to the terminal only; `--reveal-out FILE` is the
single way to write them to disk, with a warning. The shared artifacts (`parity.md`, `parity.json`, HTML, PR comment) are produced from
a result object that never holds values, so they cannot leak by construction. `--redact COL` masks columns at fetch time, so a redacted
value is not even held in memory. The same macros are exposed in the dbt package so a dbt-only user can obtain the same extract by hand.
- Alternative: always show values. Rejected: it breaks the "no data leaves the engine" promise that is the main reason to use the tool.
- Alternative: download whole differing buckets as reladiff does. Rejected: reads far more data than needed.

## Risks / Trade-offs

- [Star target is outside our control] → it is tracked as information only; every requirement is verifiable
  without it (see `community-launch`).
- [Hash drift between dbt and standalone modes] → D2 conformance corpus in CI, both modes, every release.
- [Engine coverage claims outrun testing] → engine matrix has statuses; `tested in CI` requires a passing job; most
  new engines start as `tested manually` or `untested`.
- [Cloud engines in CI cost money or need secrets] → fixtures under 1 GB per run, expiring datasets, secrets only in
  protected environments, nightly rather than per commit, owner's go before any workflow is added.
- [Maintenance load of many dialects] → one engine class per engine behind a narrow interface, contribution guide for
  adding engines, community engines labelled as such.
- [YAML parsing without dependency] → restrict the file format to a documented subset or use an optional extra.
- [`--reveal-keys` weakens the privacy story] → opt-in only, separate file, never in PR comments, bounded by a cap.
- [Scope is large for one change] → tasks are grouped so each group ships independently (distribution first).

## Migration Plan

1. Ship distribution and CI first (no behavior change), release 0.1.0 on PyPI (the version already declared in `pyproject.toml`; nothing is published yet).
2. Add the benchmark and the engine matrix, publishing the first results as they are.
3. Add standalone mode behind its own command; dbt mode untouched.
4. Add cost guard, reveal-keys, HTML report, JSON schema as opt-in options.
5. Add the GitHub Action, the Airflow operator and the hub listing.
6. Rewrite docs, record the demo, then launch per `docs/launch.md`.

Rollback: every item is additive; reverting a release means yanking the PyPI version and tagging a fix. The dbt
package is pinned by tag, so users are not moved silently.

## Open Questions

- Minimal YAML subset reader vs optional `pyyaml` extra for the connection file?
- Which cloud engines get a nightly CI job first (BigQuery only, or also Snowflake), given cost and secrets?
- Does the Airflow operator live in this repository or in a separate package?
- Resolved: dbt Hub (hubcap) needs `dbt_project.yml` at the repository root, so `dbt_package/` in a subdirectory is not listable.
  Still open: split the dbt package into its own repository, or stay in one repository and install from Git with a tag
  (see `docs/release-checklist.md`, section 4).
- How many engines at 1.0: all seven, or only those with a passing CI job?
