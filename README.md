# data-parity

Prove that a dbt model is **identical on two engines** (BigQuery, DuckDB, Trino) **without moving data**.
Each engine runs a query that prints only aggregates of per-row hashes (row counts and sums of hashes); the two
small outputs are compared. Where they differ, the next pass drills down, and only there: partition, then key
bucket, then column.

It has two parts:

- a **dbt package** (`dbt_package/`): the macros that run on each engine (`dbt run-operation ...`);
- a **cross-platform Python CLI** (`src/data_parity/`, standard library only, no dependency): it calls dbt on both
  sides, diffs the outputs, drives the drill-down and writes a Markdown recap plus a JSON result. It runs the same
  on Windows (PowerShell), macOS and Linux.

Step-by-step procedure in French: [docs/runbook.md](docs/runbook.md).

## Quick start

Requirements: Python 3.9 or newer (see the engine matrix for what was actually run), dbt on each side, and the
package installed in your dbt project (see [Install the dbt package](#install-the-dbt-package)).

```text
git clone https://github.com/camlebrun/data-parity
cd data-parity
pip install .
```

`pip install .` makes the `data-parity` command available. `pipx install data-parity` is NOT possible yet (not
published on PyPI). Without installing anything, run the module from the clone: `python -m data_parity ...`
with `src` on the Python path.

```text
# bash / zsh
PYTHONPATH=/path/to/data-parity/src python -m data_parity help
# PowerShell
$env:PYTHONPATH = "C:\path\to\data-parity\src"; python -m data_parity help
```

Then, from your dbt project directory (the same commands work in PowerShell, bash and zsh, there is no
shell-only syntax in them):

```text
data-parity conformance --a dev --b local
data-parity run orders --a dev --b local
data-parity run --a dev --b local --last 30
```

- `conformance` checks that the row hash is portable between the two engines (79 inline synthetic rows, no table
  read or written). Run it first on any new pair of engines.
- `run [MODEL ...]` compares the models (every model tagged `data_parity` if none is given). `--a` / `--b` are the
  dbt targets of the two sides.
- The same thing without the installed command: `python -m data_parity run orders --a dev --b local`.

Try it on the bundled fixtures (two DuckDB files, see [integration_tests/README.md](integration_tests/README.md)
to generate the data, and install dbt with dbt-duckdb or use dbt Fusion):

```text
cd integration_tests
dbt deps
dbt run --target engine_a
dbt run --target engine_b
data-parity conformance --a engine_a --b engine_b
data-parity run --a engine_a --b engine_b
```

The last command finds 5 differing days in `orders`, 1 column in `customers` and 1 day in `events_nopk`
(ground truth: [integration_tests/expected_findings.md](integration_tests/expected_findings.md)) and exits with 1.

### Options of `run`

| option | meaning |
|---|---|
| `--a TARGET`, `--b TARGET` | dbt targets of side A and side B (or `A=` / `B=` in `./.data-parity.env`) |
| `--dbt-a BIN`, `--dbt-b BIN` | dbt binary per side (default `dbt`); Trino needs dbt-core, DuckDB can use Fusion |
| `--project DIR`, `--project-b DIR` | dbt project directory (default: current directory); side B's, if different |
| `--profiles-dir-a DIR`, `--profiles-dir-b DIR` | dbt profiles directory per side |
| `--schema-a [DB.]SCHEMA`, `--schema-b [DB.]SCHEMA` | read each model's table in this schema on that side instead of the project's own `ref()` (see [Plain single-engine projects](#plain-single-engine-projects)) |
| `--mapping-a FILE`, `--mapping-b FILE` | file of `model: [db.]schema.table` lines: the table read for a model on that side; wins over `--schema-*` |
| `--exclude-column COL\|MODEL.COL`, `--exclude-prefix PREFIX` | columns left out on both sides (repeatable), added to `meta.data_parity`; `meta_` is always excluded unless `--no-default-exclude` |
| `--legacy a\|b`, `--label-a TEXT`, `--label-b TEXT` | which side is the legacy one (default `b`) and the side names shown in the report |
| `--as-of YYYY-MM-DD` | exclude partitions on or after this date, and NULL partitions, on both sides (a freshness gap is not a content gap) |
| `--last N` | only the N latest partitions of each model (bounds the scan cost) |
| `--buckets N` | hash buckets per partition (default 256) |
| `--drill-limit N` | max partitions drilled per model (default 200); above it a deterministic sample spread evenly from the first to the last differing partition is drilled (first and last always included); the report says "column attribution is based on a sample of N of K differing days, spread evenly from F to L" and the others read "not sampled" |
| `--detail N` | partitions listed inline in the report (default and maximum 15); the complete list of differing partitions stays in `parity.json` (aggregates only) |
| `--out DIR` | work directory; a fresh `run-<timestamp>` sub-directory is created inside (default: system temp dir / `data-parity` / project name) |
| `--report FILE`, `--json FILE` | where to write the Markdown report and the JSON result (defaults: `parity.md` and `parity.json` in the run directory) |

`data-parity report RUN_DIR [--out FILE]` re-renders `parity.md` from a saved run directory (`parity.json`, `p1_A.txt`, `p1_B.txt`) without calling dbt; exit 2 if they are missing. A run made by an older version holds an oldest-N sample, so its re-rendered "spread evenly" sentence is inaccurate: only a fresh run gives a real sample.

`.data-parity.env` holds `A=`, `B=` and `PROJECT_B=` only (parsed, never executed; binaries are never read from it).

### Plain single-engine projects

The dbt project needs no `target.type` branch, no `adapter.dispatch` and no wiring shared with the other engine: only the
`data_parity` package, the opt-in (tag or `meta.data_parity`) and tables that are already built. Models are only parsed
on each target, never compiled or run on the foreign one.

The table read for a model on one side is, in this order: the side's mapping file entry, the side's namespace
(`--schema-a` / `--schema-b`, `[database.]schema`, with the model's own table name), then the project's own `ref()`.
Nothing is hard-coded: a side that reads the repo's own relations needs no option.

```text
# one BigQuery repo; side B is Trino, whose tables live in <catalog>.<schema> (legacy side, the default)
data-parity run --a bq --b trino --dbt-b dbt-core --schema-b <catalog>.<schema> --label-a bigquery --label-b trino
# two independent projects
data-parity run --a dev --b dev --project ./a --project-b ./b
```

- **Matching**: models are matched across sides by `meta.data_parity.compare_as` when set, else by name, so `orders_dpv`
  with `compare_as: orders` is compared with `orders`. Two models of one side with the same key are an error.
- **One-sided models** (opted in on one side only, or with no table on one side) are findings, not errors: exit 1,
  `only_in_a` / `only_in_b` in `parity.json`, worded by role (`--legacy`): "only in the legacy side (not migrated yet)"
  or "only in the target side (no legacy counterpart)". If NO model of a side has a table, the namespace or mapping is
  wrong and the run ends with exit 2 naming the option.
- **Schema mismatch**: when the compared column sets (after exclusions) differ, the model is a `schema mismatch` naming
  the columns of each side (`schema_mismatch`, `columns_only_a` / `columns_only_b` in the JSON), not a data difference.
- **Column names** are matched lower-cased with every character outside `[a-z0-9_]` replaced by `_`: Trino `order/id`
  and BigQuery `order_id` are the same column and hash the same. Two columns of one side that collide are an error.
- **Tables without a partition column** are compared whole; `--last` / `--as-of` do not apply to them and the report says so.
- **dbt failures** are named by side with the cause and the fix: package missing (`dbt deps`), project that does not
  parse (file and line), unresolvable `ref`, unknown target or profile.

### Exit codes

| code | meaning |
|---|---|
| 0 | identical (for a partial scope: identical on the checked scope, see below) |
| 1 | differences found |
| 2 | error: any dbt failure, unknown target, unknown model, bad usage, unreadable output. Never 0 or 1 |

A model name that does not exist, or a dbt call that fails on one side, gives exit code 2, not "identical".

### The Markdown recap

`parity.md` (UTF-8) is written for people who were not in the room. It contains: a verdict banner; a header table
(date, engines and targets, what is compared, scope, models); a **Not checked** section (older partitions with
`--last`, partitions excluded by `--as-of`, undrilled partitions, excluded columns, untagged models); an overview
table with one line per model; per differing model a plain-language diagnosis, the ranked differing columns, the
nature of the differences (changed values or missing/extra rows), a timeline, and the table of differing partitions
with their buckets and columns (at most 15 inline; column shares are relative to the sampled days); the identical models; the exact command to
reproduce; and what the check proves and does not prove. It only contains counts, hash sums, partition values,
bucket numbers and column names, never a key or a row value. `parity.json` carries the same result for scripts
(`verdict`, `exit_code`, `verdict_text`, `scope_limited`, per-model `differing_partitions` with `kind`, `rows_a`,
`rows_b`, `differing_columns`...).

### Partial scopes are never plain "ISO"

With `--last N` or `--as-of`, or when some differing partitions were not drilled, an equal result means "identical
on the checked scope", not "identical". The tool says so everywhere: verdict text `ISO on the checked scope (...)`,
`scope_limited: true` in the JSON, a banner "Identical on a limited scope" and the **Not checked** section. Exit
code 0 in that case does NOT certify the data outside the scope.

## Constraints it is designed for

- No data leaves an engine: only counts and hash sums are printed. No key is printed by default (only partition
  values, bucket numbers and column names, when something differs).
- dbt first: everything runs as `dbt run-operation` on your existing project and profiles. The CLI only automates
  the diff and the drill-down; it never connects to a warehouse itself.
- Tables of several TB: the first pass is a single grouped scan that you can bound (`--last`, `--as-of`), and
  closed partitions can be cached.
- No engine catalog (`INFORMATION_SCHEMA` or similar) is assumed: partitions come from your dbt config.
- Cross-platform: no shell is involved (dbt is started with an argument list), so quoting of `--args` JSON is the
  same on every OS.

## Why audit_helper alone is not enough

[dbt-labs/audit_helper](https://github.com/dbt-labs/dbt-audit-helper) compares two relations that ONE dbt connection
can see, i.e. the same warehouse (two schemas, two Trino catalogs...). It cannot compare BigQuery with DuckDB, or
Trino with BigQuery, without first copying data to a place both can read. Its row-level comparisons also use
`EXCEPT` / joins over rows. data-parity instead computes a portable hash per row on each engine separately and
compares aggregates.

## How it works

1. A **canonical text** per value (`macros/canonical.sql`) and a **row hash** (`macros/row_hash.sql`): same input,
   same hash on every engine. Spec and limits: [docs/canonical-spec.md](docs/canonical-spec.md).
2. `data_parity_reconcile` prints, per model, hash aggregates in three passes:

| pass | scope | query on each engine |
|---|---|---|
| 1 | whole table | ONE grouped scan: per partition `rows`, `sum(h1)`, `sum(h2)` |
| 2 | differing partitions | per (partition, key bucket); bucket = hash of the primary key mod `buckets` |
| 3 | differing buckets | per column: sum of the hash of [pk + column] |

A model whose pass 1 is equal is never scanned again. The CLI runs pass 1 on both sides, compares, then asks for
passes 2 and 3 only where needed.

## Install the dbt package

`packages.yml` of your dbt project, then `dbt deps`:

```yaml
packages:
  - git: "https://github.com/camlebrun/data-parity"
    subdirectory: dbt_package
    # revision: <tag or commit>   # pin it once you depend on it
```

Pin `revision:` to a tag or a commit so that an update of the repository never changes your results silently. From
a local checkout: `- local: ../data-parity/dbt_package`.

## Configure models

Opt a model in with a tag. With dbt Fusion (2.x) `tags` and `meta` must be under `config:`.

```yaml
models:
  - name: orders
    config:
      tags: [data_parity]
      event_time: order_date            # used as partition column if meta.data_parity.partition_column is absent
      meta:
        data_parity:
          primary_key: [order_id]       # else inferred from a `unique` / unique_combination_of_columns test
          partition_column: order_date
          exclude_columns: [raw_payload]  # a string or a list of strings
          exclude_prefixes: [meta_]       # default; a string or a list of strings, an empty prefix is an error
          enabled: true
```

- To opt a model out: `meta: {data_parity: false}` or `enabled: false`.
- `only` and `partitions` (macro arguments) are validated: unknown model names and malformed `partitions` (anything
  but `{last: N>=1}` or a non-empty list) are compiler errors.
- Without a primary key, buckets come from the full-row hash and columns are compared over the whole partition.
- Without a partition column, pass 1 prints the table line only (table-level comparison).
- Unsupported column types (array, struct, map, json, bytes, interval, geography) stop the run with a compile error:
  exclude them via `exclude_columns`.

## Macros without the CLI

`data_parity_reconcile` arguments (all optional): `only` (models), `level` (1, 2 or 3: deepest pass allowed),
`drill` (`{model: [partition, ...]}`, pass 2), `drill_buckets` (`{model: [[partition, bucket], ...]}`, pass 3),
`buckets` (default 256, identical on both sides), `as_of`, `partitions` (`{last: N}` or an explicit list).

### Optional pure-dbt manual flow, and its false-pass trap

You can skip the CLI and diff the `PARITY|` lines yourself. **Beware: if dbt fails, both files are empty and a plain
`diff` says "identical".** The manual flow must check dbt's exit code AND that the output is not empty.

bash / zsh:

```bash
for t in a b; do
  dbt run-operation data_parity_reconcile --target "$t" > "$t.log" 2>&1 || { echo "dbt failed on $t"; exit 1; }
  sed -n 's/.*PARITY|//p' "$t.log" > "$t.txt"
  [ -s "$t.txt" ] || { echo "no PARITY output on $t"; exit 1; }
done
diff a.txt b.txt && echo IDENTICAL
```

PowerShell:

```powershell
foreach ($t in 'a', 'b') {
  dbt run-operation data_parity_reconcile --target $t *> "$t.log"
  if ($LASTEXITCODE -ne 0) { throw "dbt failed on $t" }
  Select-String -Path "$t.log" -Pattern 'PARITY\|(.*)$' |
    ForEach-Object { $_.Matches[0].Groups[1].Value } | Set-Content "$t.txt"
  if (-not (Test-Path "$t.txt") -or (Get-Item "$t.txt").Length -eq 0) { throw "no PARITY output on $t" }
}
if (Compare-Object (Get-Content a.txt) (Get-Content b.txt)) { "DIFFERENT" } else { "IDENTICAL" }
```

The CLI does these checks for you (and exits with 2 on any failure), which is the reason to prefer it.

## Cache of closed partitions (optional)

Pass 1 re-scans everything each time. `data_parity_cache_refresh` stores the fingerprint of closed partitions
(older than `reference_date - lag_days`) in a small table; `data_parity_cached_pass1` then scans only the open
ones and prints the same lines as pass 1, byte for byte; `data_parity_cache_verify` recomputes a sample to detect
silently rewritten partitions. **Writes are OFF by default** (dry run unless `write: true`, and a cache schema must
be given). Details and invalidation rules: [docs/cache.md](docs/cache.md). The cache's `reference_date` is
unrelated to reconcile's `as_of` (it only defines which partitions are closed). The CLI does not use the cache.

## Engine matrix

| Engine | Status |
|---|---|
| DuckDB | tested with dbt Fusion 2.0.9 and dbt-core 1.12 (dbt-duckdb): conformance, reconcile, cache |
| Trino | tested on Trino 484 in Docker (memory catalog) with dbt-core only (dbt Fusion does not support Trino): conformance byte-identical to DuckDB and BigQuery, reconcile DuckDB vs Trino, cache. Not tested: a real Trino cluster |
| BigQuery | inline conformance (byte-identical to DuckDB and Trino) and one real manual run of `data-parity run` with a BigQuery table as side A against a DuckDB table (read-only). No automated test of the repository covers BigQuery. The cache `bigquery__` code was never run; **cache writes on BigQuery are untested** |
| Windows | untested (the code avoids shells and POSIX-only calls, but it has never run there) |
| Python | developed and tested on one recent Python 3; 3.9 is the declared minimum and was NOT run |

## Honest limits

Known open issues:

1. **Time zone of `timestamptz` partitions**: when the partition column is a `timestamptz`, the day it falls in
   depends on the session time zone on DuckDB and Trino, while BigQuery cuts at UTC. Two engines with different
   session time zones can disagree on the partition of a row near midnight. Set the session time zone to UTC on
   DuckDB and Trino, or partition on a DATE.
2. **The cache trusts closed partitions**: a silent rewrite of a closed partition is invisible to cached pass 1 and
   only caught by `cache_verify` (a sample, not a proof) or a version bump.
3. **"Last N" on string partitions sorts the partition text**: `--last N` / `partitions: {last: N}` orders by the
   partition value as text for string partitions, which is only chronological for ISO-like values
   (`2025-01-31`, `202501`) and wrong for e.g. `1`, `2`, `10`.

Other limits:

- Floats are canonicalised as `round(x * 10^scale)` in IEEE double. If `abs(x * 10^scale) >= 2^53` the integer is
  not exactly representable and Trino differs from DuckDB / BigQuery.
- Timestamps are compared at microsecond precision; sub-microsecond data can differ (Trino rounds, DuckDB truncates).
- No unicode normalisation, no trimming, no collation: `'x '` differs from `'x'`, NFC differs from NFD.
- Sums can hide errors that cancel exactly (about 2^-24 per sum for a single changed row).
- Pass 1 is a full scan of the hashed columns. Bound it with `--last` / `--as-of`; the verdict then reads "ISO on
  the checked scope", never plain ISO.
- Partition values (dates...) are printed; use a coarse partition column if they are sensitive.
- Unsupported types must be excluded via `meta.data_parity.exclude_columns`.
- The cache partition column must be a DATE or TIMESTAMP (integer / string partitions are not cached).
- Fusion: `meta` and `tags` go under `config:`. A macro of the root project could not call package macros in the
  integration test setup (the cache test copies its reference macro into the package).

## Tests

Two layers that coexist:

- **`pytest`** (`tests/`): unit and CLI tests with a fake dbt (always run), plus `tests/test_integration.py`
  against the real fixtures when `DBT_FUSION` or `DBT_CORE` points to a dbt binary. Run it after any change to
  `src/`:

  ```text
  python -m pytest -q
  DBT_FUSION=dbt python -m pytest -q       # PowerShell: $env:DBT_FUSION = "dbt"; python -m pytest -q
  ```

- **Shell scripts** (`integration_tests/`, POSIX `sh` / `bash`): the full matrix, including dbt-core and Trino,
  the cache and the config guards. Run them before a release or after any change to `dbt_package/`:

  ```text
  sh   integration_tests/test_reconcile.sh        # CLI end to end: Fusion, dbt-core, Trino (TRINO=1)
  bash integration_tests/test_cache.sh
  bash integration_tests/test_config_guards.sh
  ```

  `TRINO=1` adds the Trino scenarios, which need the Docker container on `localhost:18080`. Nothing touches
  BigQuery. See the READMEs in `integration_tests/`.

MIT license.
