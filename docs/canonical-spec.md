# Canonical form and row hash

Goal: prove two engines hold the same table by comparing aggregates of per-row hashes, without moving data.
Each engine computes `count(*), sum(h1), sum(h2)` (per group) over `data_parity_row_hash_parts(columns)`.

Engines validated by `dbt run-operation data_parity_conformance` (inline synthetic rows, 8 typed columns, no table read or written; the real output is 79 `PARITY|` lines, the 1e22-class float cases are not part of the set):
DuckDB (dbt Fusion 2.0.9 and dbt-core/duckdb 1.11), BigQuery (Fusion), Trino 484 (dbt-core/dbt-trino 1.10.6, Docker, memory catalog).
Result: the `PARITY|` outputs are byte-identical for DuckDB/BigQuery, BigQuery/Trino, DuckDB/Trino (79 lines; the 1e22-class float cases are not in the set, see Floats).
Not tested: real Trino clusters (only the Docker Trino 484 with the memory catalog), and BigQuery through the cache/reconcile macros (inline conformance only).
Rules are implemented in `macros/canonical.sql` (`default__` = Trino/ANSI, `duckdb__`, `bigquery__`).

## Canonical text per type (`data_parity_canonical(col, data_type, scale=6)`)

| Type class | Rule | Notes / limits |
|---|---|---|
| NULL | sentinel `\N` (two chars) | A real string equal to `\N` has the same canonical text. The row hash does NOT confuse them (NULL is hashed as `name:N`, values as `name:len:text`). |
| boolean | `1` / `0` | |
| integers (tinyint..bigint, int64, hugeint) | decimal text | Unsigned 64-bit / hugeint beyond int64 only if the engine has them. |
| decimal / numeric / bignumeric | text of the integer `round_half_away(x, scale) * 10^scale` | Rounding: half away from zero (verified: -0.0000005 -> -1, 0.0000005 -> 1, 0.0000004999 -> 0). Computed exactly in decimal arithmetic (DuckDB hugeint, BigQuery BIGNUMERIC, Trino decimal(38,0)). Beyond 38 digits after scaling: engine error (not silently wrong). 1e15 (-> 10^21, past 2^63) works. |
| float / double / real | text of the integer `round_half_away(x * 10^scale)`, computed in IEEE double; `NaN`, `Inf`, `-Inf` as words; `-0.0` -> `0` | Floats are never printed raw. Float32 is widened to double first (engines widen identically, but a float32 column compared with a float64 column is NOT equal). **Limit**: if `abs(x * 10^scale) >= 2^53` the integer is not exactly representable: DuckDB/BigQuery print the exact double value (`1e22` -> `9999999999999999583119736832`), Trino prints the shortest decimal (`10000000000000000000000000000`). Not identical; excluded from the conformance set. Within 2^53 all three agree. Values exactly on a .5 boundary after the multiplication follow IEEE and agree because the multiply is the same IEEE operation. |
| date | `YYYY-MM-DD` (zero padded year, 4 digits) | BigQuery `%Y` does not pad year 1: handled by explicit `lpad`. Years outside 0001-9999 untested. |
| timestamp / timestamptz / datetime | epoch microseconds UTC, integer text | Naive types (DuckDB/Trino `timestamp`, BigQuery `datetime`, `... without time zone`) are read as UTC. BigQuery bare `TIMESTAMP` is an instant. Tested range 1900 to 9999-12-31 23:59:59.999999, negative epochs included. Sub-micro precision: DuckDB `epoch_us` truncates, BigQuery has no sub-micro, **Trino `cast(... as timestamp(6))` rounds** (not truncated): nanosecond/picosecond columns can differ on Trino. Not tested (conformance data is micro precision). A naive timestamp compared to a tz-aware one is equal only if the session/data is UTC. |
| string | as-is (UTF-8 code points) | No collation, no trim, no unicode normalisation: `'x '` != `'x'`, `''` != NULL, NFC `é` != NFD `e + U+0301` (different hashes, verified). Case-insensitive collations on the source (e.g. MySQL, BigQuery `COLLATE`) do not change the stored bytes and are not applied. CHAR(n) padding in Trino/DuckDB `CHAR` differs from BigQuery: cast to varchar/string upstream. Lone surrogates / invalid UTF-8 untested. Strings containing a newline are fine for hashing but break the line oriented conformance output (not in the test set). |
| array, struct, map, json, bytes, interval, geography, anything else | compile error naming the type | `data_parity: unsupported column type '...'` |

Type names are matched case-insensitively on the base name (parameters after `(` ignored): BIGINT, INT64, DECIMAL(21,1), NUMERIC, FLOAT64, DOUBLE, VARCHAR, STRING, DATE, TIMESTAMP, TIMESTAMP WITH TIME ZONE, DATETIME, BOOL...
Wrong hint (e.g. float column declared as decimal) changes only the arithmetic path, so declare types faithfully.

## Row hash (`data_parity_row_hash_parts(columns)`)

- Columns are sorted by `lower(name)`; two columns differing only by case are not supported (one wins).
- Row string = pieces joined with `|`. Piece = `<lower(name)>:N` when NULL, else `<lower(name)>:<length>:<canonical text>` (`length` = character count, so `|` or digits inside strings cannot create collisions and no escaping is needed).
- `h1` = hex chars 1-6 of `md5(row string)` as an integer, `h2` = chars 7-12. Both in [0, 2^24).
- `sum(h1)`, `sum(h2)` over a group cannot overflow int64 below about 2^39 rows; they are order independent. 12 hex chars = 48 bits of md5 per row plus count: collision risk of an undetected difference is ~2^-24 per independent sum for a single changed row; use several grouping levels (or a salted second run) for stronger guarantees. Multiset differences that cancel in a sum (e.g. swap of values between two rows with the same hash) are impossible to detect by sums alone only when the row hashes coincide.
- The md5 is evaluated twice per row (once per expression); engines usually CSE it.
- Hex to int: DuckDB `('0x'||substr(md5(s),..))::bigint`; BigQuery `cast(concat('0x', substr(to_hex(md5(s)),..)) as int64)`; Trino `from_base(substr(lower(to_hex(md5(to_utf8(s)))),..),16)`. All three verified.
- MiniJinja (Fusion) and Jinja2 (dbt-core) both run the macros (DuckDB output identical under both).

## Known cross-engine non-equalities (not forced)

1. Floats with `abs(x*10^scale) >= 2^53`: Trino differs from DuckDB/BigQuery (see above).
2. Trino timestamps beyond microseconds are rounded, others truncated.
3. Strings: any collation/normalisation/padding difference is a real difference and is reported as such.
4. Decimals exceeding 38 digits after scaling error out.
5. The conformance set avoids single quotes inside literals and backslashes (BigQuery and ANSI literal escaping differ), so these inputs are not covered by the hash test (hash itself does not depend on them).
