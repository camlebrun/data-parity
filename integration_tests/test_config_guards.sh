#!/usr/bin/env bash
# Config-guard regression tests (bad / ambiguous arguments and model configs must fail loudly or be
# interpreted correctly). DuckDB only; everything runs in a throw-away copy, fixtures are never edited.
#
#   PY=/path/to/python-with-duckdb [DBT=dbt] bash integration_tests/test_config_guards.sh
#
# Covers: string exclude_prefixes / exclude_columns, `only` (string, unknown names), `partitions`
# shapes, cached_pass1 as_of/partitions, NULL partition sentinel (cache == reconcile), write flag,
# schema / lag_days validation, data_parity: false, target.type guard (simulated).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG="$HERE/../dbt_package"
DBT="${DBT:-dbt}"
PY="${PY:-python3}"
ARGS="schema: dp_cache, reference_date: 2025-04-10"

W="$(mktemp -d)"
[ "${KEEP:-0}" = 1 ] || trap 'rm -rf "$W"' EXIT
echo "work dir: $W"
mkdir -p "$W/data"
cp -R "$HERE/dbt_project.yml" "$HERE/models" "$W/"
cp "$HERE"/data/engine_a.duckdb "$W/data/"
cat > "$W/profiles.yml" <<'EOF'
dp_integration:
  target: engine_a
  outputs:
    engine_a: {type: duckdb, path: data/engine_a.duckdb, schema: dp_out}
EOF
printf 'packages:\n  - local: %s\n' "$(cd "$PKG" && pwd)" > "$W/packages.yml"
cd "$W"
$DBT deps >/dev/null 2>&1
rm -rf dbt_packages/data_parity; mkdir -p dbt_packages
cp -R "$PKG" dbt_packages/data_parity
# build the models (views over the source tables) so the copy does not depend on a previous `dbt run`
$DBT run --target engine_a >/dev/null 2>&1 || { echo "dbt run failed on engine_a" >&2; exit 2; }

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  PASS  $*"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL  $*"; }
P()   { sed -n 's/.*PARITY|//p'; }
# run: sets RC and OUT
run() { OUT=$($DBT run-operation "$1" --target engine_a --args "{$2}" 2>&1); RC=$?; }
# expect_err "<description>" <macro> "<args>" "<error text>"
expect_err() { run "$2" "$3"; if [ $RC -ne 0 ] && printf '%s' "$OUT" | grep -qF -- "$4"; then ok "$1"; else bad "$1 (rc=$RC, wanted: $4)"; echo "$OUT" | tail -3; fi; }
expect_ok()  { run "$2" "$3"; if [ $RC -eq 0 ]; then ok "$1"; else bad "$1 (rc=$RC)"; echo "$OUT" | tail -3; fi; }
# models yml with the given meta for orders (line(s) under `meta:`), and customers meta
set_models() { # orders_meta customers_config_extra
  cat > models/_models.yml <<EOF
version: 2
models:
  - name: orders
    config:
      tags: [data_parity]
      event_time: order_date
      meta:
        data_parity: $1
    columns:
      - name: order_id
        data_tests: [unique]
  - name: customers
    config:
      tags: [data_parity]
$2
    columns:
      - name: customer_id
        data_tests: [unique]
  - name: events_nopk
    config:
      tags: [data_parity]
      event_time: order_date
EOF
}
cols_of() { printf '%s\n' "$OUT" | P | sed -n '/^## orders$/,/^table|/p' | grep '^cols|'; }

echo "== F1 exclude_prefixes / exclude_columns given as a string"
set_models '{exclude_prefixes: "meta_"}' ''
run data_parity_reconcile "only: [orders], level: 1"
c=$(cols_of)
if [ $RC -eq 0 ] && ! echo "$c" | grep -q 'meta_' && echo "$c" | grep -q 'amount:' && echo "$c" | grep -q 'order_id:'; then ok "string prefix 'meta_' excludes meta_* only (amount kept)"; else bad "string prefix: $c"; fi
printf '%s\n' "$OUT" | grep -q 'NOTE|orders|excluded=meta_loaded_at' && ok "NOTE|orders|excluded=meta_loaded_at printed (reconcile)" || bad "excluded note (reconcile)"
set_models '{exclude_columns: "amount"}' ''
run data_parity_reconcile "only: [orders], level: 1"
c=$(cols_of)
if [ $RC -eq 0 ] && ! echo "$c" | grep -q 'amount:' && echo "$c" | grep -q 'status:' && echo "$c" | grep -q 'order_id:'; then ok "string exclude_columns 'amount' excludes exactly that column"; else bad "string exclude_columns: $c"; fi
set_models '{exclude_prefixes: ""}' ''
expect_err "empty prefix '' rejected (reconcile)" data_parity_reconcile "only: [orders], level: 1" "non-empty strings"
expect_err "empty prefix '' rejected (cache)" data_parity_cache_refresh "only: [orders], $ARGS" "non-empty strings"
set_models '{exclude_prefixes: [""]}' ''
expect_err "empty prefix in list rejected" data_parity_reconcile "only: [orders], level: 1" "non-empty strings"
set_models '{exclude_prefixes: 5}' ''
expect_err "numeric exclude_prefixes rejected" data_parity_reconcile "only: [orders], level: 1" "must be a string or a list of strings"
set_models '{exclude_columns: {a: 1}}' ''
expect_err "mapping exclude_columns rejected (cache)" data_parity_cache_refresh "only: [orders], $ARGS" "must be a string or a list of strings"
set_models '{exclude_prefixes: "meta_"}' ''
run data_parity_cached_pass1 "only: [orders], $ARGS"
printf '%s\n' "$OUT" | grep -q 'NOTE|orders|excluded=meta_loaded_at' && ! (printf '%s\n' "$OUT" | P | grep '^cols|' | grep -q 'meta_') && ok "cache: string prefix handled, excluded note printed" || bad "cache string prefix"

echo "== F10 data_parity: false disables a model"
set_models 'true' '      meta:
        data_parity: false'
run data_parity_reconcile "level: 1"
if [ $RC -eq 0 ] && ! printf '%s\n' "$OUT" | P | grep -q '^## customers$' && printf '%s\n' "$OUT" | P | grep -q '^## orders$'; then ok "reconcile skips customers (data_parity: false)"; else bad "data_parity:false (reconcile)"; fi
run data_parity_cached_pass1 "$ARGS"
if [ $RC -eq 0 ] && ! printf '%s\n' "$OUT" | P | grep -q '^## customers$'; then ok "cache skips customers (data_parity: false)"; else bad "data_parity:false (cache)"; fi
set_models 'true' '      meta:
        data_parity: {enabled: false}'
run data_parity_reconcile "level: 1"
! printf '%s\n' "$OUT" | P | grep -q '^## customers$' && ok "enabled: false still disables" || bad "enabled:false"
set_models 'true' ''

echo "== F3 only"
run data_parity_reconcile "only: orders, level: 1"
if [ $RC -eq 0 ] && printf '%s\n' "$OUT" | P | grep -q '^## orders$' && ! printf '%s\n' "$OUT" | P | grep -q '^## customers$'; then ok "only: orders (string) = exactly orders"; else bad "only as string"; fi
expect_err "only: [orders, nope] -> error naming 'nope'" data_parity_reconcile "only: [orders, nope], level: 1" "names no tagged model: nope"
expect_err "only: ord (substring) -> error, not a substring match" data_parity_reconcile "only: ord, level: 1" "names no tagged model: ord"
expect_err "cache: only unknown -> error" data_parity_cache_refresh "only: [nope], $ARGS" "names no tagged model: nope"
expect_err "cache: only: ord (substring) -> error" data_parity_cached_pass1 "only: ord, $ARGS" "names no tagged model: ord"
expect_err "only given as a number" data_parity_reconcile "only: 5, level: 1" "must be a string or a list"

echo "== F2 partitions"
expect_err "partitions {lst: 5}" data_parity_reconcile "only: [orders], partitions: {lst: 5}" "partitions must be"
expect_err "partitions {}" data_parity_reconcile "only: [orders], partitions: {}" "partitions must be"
expect_err "partitions []" data_parity_reconcile "only: [orders], partitions: []" "partitions must be"
expect_err "partitions {last: 0}" data_parity_reconcile "only: [orders], partitions: {last: 0}" "partitions must be"
expect_err "partitions {last: -2}" data_parity_reconcile "only: [orders], partitions: {last: -2}" "partitions must be"
expect_err "partitions {last: abc}" data_parity_reconcile "only: [orders], partitions: {last: abc}" "partitions must be"
expect_err "partitions {last: 1.5}" data_parity_reconcile "only: [orders], partitions: {last: 1.5}" "partitions must be"
expect_err "partitions {last: 2, extra: 1}" data_parity_reconcile "only: [orders], partitions: {last: 2, extra: 1}" "partitions must be"
expect_err "partitions: abc (string)" data_parity_reconcile "only: [orders], partitions: abc" "partitions must be"
expect_err "partitions list matching nothing -> 0 checked" data_parity_reconcile "only: [orders], partitions: ['1900-01-01']" "selected 0 of"
run data_parity_reconcile "only: [orders], partitions: {last: 2}, level: 1"
printf '%s\n' "$OUT" | P | grep -q '^scope|last=2|checked=2|' && ok "partitions {last: 2} -> checked=2" || bad "partitions last 2"

echo "== F5 cached_pass1 refuses as_of / partitions; NULL partition sentinel"
expect_err "cached_pass1 as_of rejected" data_parity_cached_pass1 "$ARGS, as_of: '2025-03-01'" "does not support as_of / partitions"
expect_err "cached_pass1 partitions rejected" data_parity_cached_pass1 "$ARGS, partitions: {last: 3}" "does not support as_of / partitions"
$PY - "$W/data/engine_a.duckdb" <<'EOF'
import duckdb, sys
c = duckdb.connect(sys.argv[1])
c.execute("update dp_out.orders set order_date = null where order_id = (select min(order_id) from dp_out.orders)")
c.close()
EOF
run data_parity_reconcile "only: [orders], level: 1"
printf '%s\n' "$OUT" | P | grep -v -e '^pk|' -e '^note|' > rc.txt
run data_parity_cached_pass1 "only: [orders], $ARGS"
printf '%s\n' "$OUT" | P | grep -v -e '^pk|' -e '^note|' > cp.txt
if grep -q '^part|\\N|' rc.txt && grep -q '^part|\\N|' cp.txt && ! grep -q 'part|NULL' cp.txt && cmp -s rc.txt cp.txt; then ok "NULL partition printed as part|\\N by both; cached_pass1 == reconcile byte for byte"; else bad "NULL sentinel / identity"; diff rc.txt cp.txt | head -5; fi

echo "== F9 write flag, schema, lag_days"
for v in false False FALSE "'false'"; do
  run data_parity_cache_refresh "only: [orders], $ARGS, write: $v"
  if [ $RC -eq 0 ] && printf '%s\n' "$OUT" | grep -q 'mode=DRY RUN'; then ok "write: $v is a dry run"; else bad "write: $v"; fi
done
expect_err "write: maybe rejected" data_parity_cache_refresh "only: [orders], $ARGS, write: maybe" "write must be true or false"
expect_err "write: 0 rejected" data_parity_cache_refresh "only: [orders], $ARGS, write: 0" "write must be true or false"
expect_err "fail_on_stale: maybe rejected" data_parity_cache_verify "only: [orders], schema: dp_cache, fail_on_stale: maybe" "fail_on_stale must be true or false"
expect_err "schema with quote/semicolon rejected" data_parity_cache_refresh "only: [orders], schema: \"x'; drop table t; --\", reference_date: 2025-04-10" "invalid schema"
expect_err "schema with dot rejected" data_parity_cache_refresh "only: [orders], schema: 'a.b', reference_date: 2025-04-10" "invalid schema"
expect_err "lag_days -1 rejected" data_parity_cache_refresh "only: [orders], $ARGS, lag_days: -1" "lag_days must be an integer >= 0"
expect_err "lag_days abc rejected" data_parity_cached_pass1 "only: [orders], $ARGS, lag_days: abc" "lag_days must be an integer >= 0"
expect_ok  "schema with hyphen is accepted (dry run)" data_parity_cache_refresh "only: [orders], schema: dp-cache, reference_date: 2025-04-10"

echo "== F8 target.type guard (simulated: duckdb removed from the allowed list in a copy of the package)"
sed -i.bak "s/\['duckdb', 'bigquery', 'trino'\]/['bigquery', 'trino']/" dbt_packages/data_parity/macros/canonical.sql
grep -q "\['bigquery', 'trino'\]" dbt_packages/data_parity/macros/canonical.sql || bad "could not patch the guard list"
expect_err "unsupported target.type message (reconcile)" data_parity_reconcile "only: [orders], level: 1" "data_parity supports duckdb, bigquery and trino only (target.type=duckdb)"
expect_err "unsupported target.type message (cache)" data_parity_cache_refresh "only: [orders], $ARGS" "data_parity supports duckdb, bigquery and trino only (target.type=duckdb)"
cp "$PKG/macros/canonical.sql" dbt_packages/data_parity/macros/canonical.sql; rm -f dbt_packages/data_parity/macros/canonical.sql.bak

echo "passed=$PASS failed=$FAIL"
[ "$FAIL" -eq 0 ] && echo "ALL CHECKS PASSED" || echo "SOME CHECKS FAILED"
exit "$FAIL"
