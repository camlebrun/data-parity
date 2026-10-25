#!/usr/bin/env bash
# End-to-end test of the closed-partition cache (dbt_package/macros/cache.sql).
#
#   bash integration_tests/test_cache.sh
#
# Everything runs in a throw-away work directory: the fixtures in integration_tests/data
# are COPIED, the original engine_a/engine_b files are never written. Nothing but DuckDB
# files is touched (no BigQuery, no Trino here; Trino is covered by test_cache_trino.sh).
#
# Env:
#   DBT      dbt binary used for the tests            (default: dbt)
#   DBT2     second dbt binary, run on the identity test and used to read the SQL log
#            (Jinja2 dbt-core gives the compiled SQL of run_query in logs/dbt.log)   (default: none)
#   PY       python with the duckdb module (to mutate the COPY of engine_b)          (required)
#   KEEP=1   keep the work directory
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG="$HERE/../dbt_package"
DBT="${DBT:-dbt}"
DBT2="${DBT2:-}"
PY="${PY:-python3}"
REF_DATE="2025-04-10"       # lag 3 -> cutoff 2025-04-07: 96 closed days, 24 open days in `orders`
ARGS="schema: dp_cache, reference_date: $REF_DATE"

W="$(mktemp -d)"
[ "${KEEP:-0}" = 1 ] || trap 'rm -rf "$W"' EXIT
echo "work dir: $W"
mkdir -p "$W/data"
cp -R "$HERE/dbt_project.yml" "$HERE/models" "$W/"
cp "$HERE"/data/engine_a.duckdb "$HERE"/data/engine_b.duckdb "$W/data/"
cat > "$W/profiles.yml" <<'EOF'
dp_integration:
  target: engine_a
  outputs:
    engine_a: {type: duckdb, path: data/engine_a.duckdb, schema: dp_out}
    engine_b: {type: duckdb, path: data/engine_b.duckdb, schema: dp_out}
EOF
printf 'packages:\n  - local: %s\n' "$(cd "$PKG" && pwd)" > "$W/packages.yml"
cd "$W"
$DBT deps >/dev/null 2>&1
# Use a real copy of the package (fresh at each run) and add an independent live reference
# macro to it. (A macro in the root project cannot call the package's macros.)
rm -rf dbt_packages/data_parity; mkdir -p dbt_packages
cp -R "$PKG" dbt_packages/data_parity
# build the models (views over the source tables) so the copy does not depend on a previous `dbt run`
for t in engine_a engine_b; do
  $DBT run --target "$t" >/dev/null 2>&1 || { echo "dbt run failed on $t" >&2; exit 2; }
done
cat > dbt_packages/data_parity/macros/ref_pass1.sql <<'EOF'
{# Independent live reference of pass 1: ONE plain grouped scan per model, no cache code. #}
{% macro ref_pass1() %}
  {% set out = [] %}
  {% for node in graph.nodes.values() | sort(attribute='name') if node.resource_type == 'model' %}
    {% set rel = ref(node.name) %}
    {% set cols = [] %}{% set cl = [] %}
    {% for c in adapter.get_columns_in_relation(rel) | sort(attribute='name') if not (c.name | lower).startswith('meta_') %}
      {% do cols.append({'name': c.name, 'data_type': c.data_type}) %}
      {% do cl.append((c.name | lower) ~ ':' ~ (data_parity_type_class(c.data_type) | trim)) %}
    {% endfor %}
    {% set p = data_parity_row_hash_parts(cols) %}
    {% set pc = node.config.get('event_time') %}
    {% set t = run_query('select count(*), sum(' ~ p.h1 ~ '), sum(' ~ p.h2 ~ ') from ' ~ rel).rows[0] %}
    {% do out.append('PARITY|## ' ~ node.name) %}
    {% do out.append('PARITY|cols|' ~ cl | join(',')) %}
    {% do out.append('PARITY|table|rows=' ~ t[0] | int ~ '|h1=' ~ t[1] | int ~ '|h2=' ~ t[2] | int) %}
    {% if pc %}
      {% for r in run_query('select cast(cast(' ~ pc ~ ' as date) as varchar), count(*), sum(' ~ p.h1 ~ '), sum(' ~ p.h2 ~ ') from ' ~ rel ~ ' group by 1 order by 1').rows %}
        {% do out.append('PARITY|part|' ~ r[0] ~ '|rows=' ~ r[1] | int ~ '|h1=' ~ r[2] | int ~ '|h2=' ~ r[3] | int) %}
      {% endfor %}
    {% endif %}
  {% endfor %}
  {{ log(out | join('\n'), info=true) }}
{% endmacro %}
EOF

PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "  PASS  $*"; }
bad()  { FAIL=$((FAIL+1)); echo "  FAIL  $*"; }
P()    { sed -n 's/.*PARITY|//p'; }
# pk| / note| lines are mirrored from data_parity_reconcile when installed; the plain reference has none
PC()   { P | grep -v -e '^pk|' -e '^note|'; }
N()    { grep -o 'NOTE|.*\|VERIFY|.*'; }
# run a macro: op <bin> <macro> <target> '<args>' [extra dbt flags]
op()   { local b="$1" m="$2" t="$3" a="$4"; shift 4; $b run-operation "$m" --target "$t" --args "{$a}" "$@" 2>&1; }
# scalar from a query on a target (dbt show, json)
sql1() { $DBT show --inline "$1" --target "$2" --output json 2>&1 | grep -o '^\[{.*}\]' | $PY -c 'import json,sys; print(list(json.load(sys.stdin)[0].values())[0])'; }
# mutate the COPY of an engine
mutate() { $PY - "$W/data/$1.duckdb" "$2" <<'EOF'
import duckdb, sys
c = duckdb.connect(sys.argv[1]); c.execute(sys.argv[2]); c.close()
EOF
}
CACHE_N() { sql1 "select count(*) from $1.dp_cache.data_parity_cache" "$1"; }

echo "== reference (live full pass 1, plain grouped query) =="
for t in a b; do op $DBT ref_pass1 engine_$t "" | P > ref_$t.txt; done
echo "  reference lines: $(wc -l < ref_a.txt) (engine_a), $(wc -l < ref_b.txt) (engine_b)"
[ -s ref_a.txt ] || { echo "reference empty, abort"; exit 2; }

echo "== (a) dry run creates nothing =="
op $DBT data_parity_cache_refresh engine_a "$ARGS" | N > a_dry.txt
grep -q 'DRY RUN' a_dry.txt && grep -q 'WOULD compute+insert missing' a_dry.txt && ok "dry run prints missing partitions" || bad "dry run output"
n=$(sql1 "select count(*) from duckdb_schemas() where schema_name = 'dp_cache'" engine_a)
[ "$n" = 0 ] && ok "schema dp_cache does not exist after dry run" || bad "dry run created schema ($n)"
# write=true without any schema must refuse (no default dataset)
op $DBT data_parity_cache_refresh engine_a "write: true, reference_date: $REF_DATE" | grep -q 'no cache schema' && ok "write without schema is refused" || bad "write without schema not refused"

echo "== (b) refresh write=true fills only closed partitions =="
op $DBT data_parity_cache_refresh engine_a "$ARGS, write: true" | N > b_write.txt
n=$(CACHE_N engine_a); hi=$(sql1 "select max(partition_value) from engine_a.dp_cache.data_parity_cache" engine_a)
echo "  cache rows: $n (96 orders + 96 events_nopk expected), max cached partition: $hi (< cutoff 2025-04-07)"
[ "$n" = 192 ] && [ "$hi" = "2025-04-06" ] && ok "192 rows, only closed partitions" || bad "cache content ($n, $hi)"

echo "== (c) cached_pass1 byte-identical to live full pass 1 =="
for t in a; do
  op $DBT data_parity_cached_pass1 engine_$t "$ARGS" > c_$t.raw; PC < c_$t.raw > c_$t.txt
  cmp -s c_$t.txt ref_$t.txt && ok "engine_$t: $(wc -l < c_$t.txt) PARITY lines identical (Fusion)" || { bad "engine_$t differs"; diff c_$t.txt ref_$t.txt | head; }
done
if [ -f "$PKG/macros/reconcile.sql" ]; then
  op $DBT data_parity_reconcile engine_a "level: 1" | P > rc_a.txt
  P < c_a.raw | cmp -s - rc_a.txt && ok "engine_a: also byte-identical to data_parity_reconcile level 1 ($(wc -l < rc_a.txt) lines, incl. pk| lines)" || { bad "differs from data_parity_reconcile"; P < c_a.raw | diff - rc_a.txt | head -5; }
fi
N < c_a.raw | grep -v 'WOULD' > c_a.notes; cat c_a.notes | sed 's/^/    /'
if [ -n "$DBT2" ]; then
  op $DBT2 data_parity_cached_pass1 engine_a "$ARGS" | PC > c_a2.txt
  cmp -s c_a2.txt ref_a.txt && ok "engine_a: identical with $DBT2 (Jinja2)" || bad "engine_a differs under $DBT2"
fi
# engine_b: own cache, identical too
op $DBT data_parity_cache_refresh engine_b "$ARGS, write: true" | N > /dev/null
op $DBT data_parity_cached_pass1 engine_b "$ARGS" | PC > c_b.txt
cmp -s c_b.txt ref_b.txt && ok "engine_b: identical to its live pass 1" || bad "engine_b differs"
# the cache is no contract trick: a and b must still differ exactly on the expected findings
diff <(grep '^part|' c_a.txt) <(grep '^part|' c_b.txt) | grep '^<' | cut -d'|' -f2 | sort -u | tr '\n' ' ' | sed 's/^/  differing days a vs b (cached): /;s/$/\n/'

echo "== (d) second refresh adds nothing =="
op $DBT data_parity_cache_refresh engine_a "$ARGS, write: true" | N > d.txt
grep -c 'up to date, nothing to do' d.txt | grep -q 2 && [ "$(CACHE_N engine_a)" = 192 ] && ok "no partition added (still 192 rows)" || { bad "second refresh"; cat d.txt; }

echo "== (g) later runs scan only open partitions =="
if [ -n "$DBT2" ]; then
  rm -rf logs
  op $DBT2 data_parity_cached_pass1 engine_a "$ARGS" > /dev/null
  $PY - logs/dbt.log <<'EOF'
import re, sys
t = open(sys.argv[1]).read()
agg = re.findall(r'count\(\*\) as n.*?from "[^"]+"\."[^"]+"\."(\w+)"( where [^\n]*)?', t, re.S)
bad = 0
for tbl, where in agg:
    print(f"    hash-scan of {tbl}: {where.strip() or 'NO FILTER (full table)'}")
    if tbl != "customers" and ">= date '2025-04-07'" not in where:
        bad += 1
print("    unfiltered/closed hash scans on partitioned tables:", bad)
sys.exit(1 if bad else 0)
EOF
  [ $? = 0 ] && ok "only the open range (>= cutoff) is hash-scanned" || bad "closed partitions were scanned"
else
  echo "  skipped (needs DBT2 for the SQL log)"
fi

echo "== (e) rewrite of a closed partition: stale cache hides it, verify catches it =="
mutate engine_b "update dp_out.orders set status = 'REWRITTEN' where order_date = '2025-02-05' and order_id % 7 = 0"
op $DBT ref_pass1 engine_b "" | P > ref_b2.txt
op $DBT data_parity_cached_pass1 engine_b "$ARGS" | PC > c_b2.txt
if ! cmp -s ref_b2.txt ref_b.txt && cmp -s c_b2.txt ref_b.txt && ! cmp -s c_b2.txt ref_b2.txt; then
  ok "trusting the cache silently hides the corruption (cached pass 1 == OLD data, live pass 1 changed)"
  diff c_b2.txt ref_b2.txt | grep '^>' | cut -c1-60 | sed 's/^/    live only: /'
else bad "mutation test premise"; fi
op $DBT data_parity_cache_verify engine_b "$ARGS, sample: 1000, fail_on_stale: false" | N > e_verify.txt
grep 'VERIFY|STALE' e_verify.txt | sed 's/^/    /'
[ "$(grep -c 'VERIFY|STALE' e_verify.txt)" = 1 ] && grep -q 'VERIFY|STALE|orders|2025-02-05' e_verify.txt && [ "$(grep -c 'VERIFY|OK' e_verify.txt)" = 191 ] \
  && ok "verify reports exactly 1 STALE (orders 2025-02-05) and 191 OK" || bad "verify result"
op $DBT data_parity_cache_verify engine_b "$ARGS, sample: 1000" | grep -q 'no longer match the live data' && ok "verify fails (non-zero) by default on STALE" || bad "verify did not fail"
op $DBT data_parity_cache_verify engine_a "$ARGS, sample: 5" | N | tail -1 | grep -q 'stale_partitions=0' && ok "verify on untouched engine_a: 0 stale" || bad "verify engine_a"
# recovery: drop the stale cached row (or bump version) and refresh
mutate engine_b "delete from dp_cache.data_parity_cache where model='orders' and partition_value='2025-02-05'"
op $DBT data_parity_cache_refresh engine_b "$ARGS, write: true" | N | grep -q 'inserted=1' && op $DBT data_parity_cached_pass1 engine_b "$ARGS" | PC | cmp -s - ref_b2.txt \
  && ok "after deleting the stale row + refresh, cached pass 1 == new live" || bad "recovery"

echo "== (f) schema change / version bump => recompute =="
mutate engine_a "alter table dp_out.orders add column extra_col varchar"
op $DBT ref_pass1 engine_a "" | P > ref_a3.txt
op $DBT data_parity_cached_pass1 engine_a "$ARGS" > f1.raw; PC < f1.raw > f1.txt
cmp -s f1.txt ref_a3.txt && ! cmp -s f1.txt ref_a.txt && ok "new column: output == new live pass 1 (cache entries of orders not trusted)" || bad "new column handling"
N < f1.raw | grep orders | sed 's/^/    /'
[ "$(CACHE_N engine_a)" = 192 ] && ok "write=false pass1 stored nothing (still 192 rows)" || bad "pass1 wrote to cache"
op $DBT data_parity_cache_refresh engine_a "$ARGS" | N | grep orders | grep -E 'stale=96' | head -1 | sed 's/^/    /' | grep -q stale && ok "dry run sees 96 stale orders partitions" || bad "dry run stale"
op $DBT data_parity_cache_refresh engine_a "$ARGS, write: true" | N | grep -q 'inserted=96 (superseding stale=96, prune=false)' && [ "$(CACHE_N engine_a)" = 288 ] && ok "refresh appended 96 new rows (default append-only: 192+96=288; newest row wins)" || bad "refresh stale append"
op $DBT data_parity_cached_pass1 engine_a "$ARGS" > f2.raw; PC < f2.raw | cmp -s - ref_a3.txt && N < f2.raw | grep -q 'orders.*from_cache=96.*live_closed_not_trusted=0' && ok "after refresh: identical again, 96 from cache" || bad "after refresh"
op $DBT data_parity_cached_pass1 engine_a "$ARGS" --vars '{data_parity_cache_version: "2"}' > f3.raw; PC < f3.raw | cmp -s - ref_a3.txt && N < f3.raw | grep -q 'orders.*from_cache=0.*live_closed_not_trusted=96' && ok "version bump: nothing trusted, identical output via live scan" || bad "version bump"
op $DBT data_parity_cache_refresh engine_a "$ARGS, write: true" --vars '{data_parity_cache_version: "2", data_parity_cache_prune: true}' | N | grep -q 'inserted=96 (superseding stale=96, prune=true)' && [ "$(CACHE_N engine_a)" = 192 ] \
  && [ "$(sql1 "select count(*) from engine_a.dp_cache.data_parity_cache where version='2'" engine_a)" = 192 ] && ok "version 2 + prune=true: superseded rows deleted, 192 rows all version 2" || bad "version refresh"

# ---------------------------------------------------------------------------------------------
# Trino section (opt-in): TRINO=1 PY=<python with the `trino` module> DBT2=<dbt-core with dbt-trino>
# Needs a Trino at http://localhost:18080 (user dbt, catalog memory). dbt Fusion does not support Trino,
# so only DBT2 is used. Creates and drops its own tiny tables in memory.dp_cache_src / dp_cache_test / dp_cache_c.
# ---------------------------------------------------------------------------------------------
if [ "${TRINO:-0}" = 1 ]; then
  echo "== Trino (memory connector), dbt-core =="
  T="$W/trino"; mkdir -p "$T/models" "$T/dbt_packages"; cd "$T"
  tq() { $PY - "$1" <<'EOF' 2>&1 | grep -v keyring
import trino, sys
c = trino.dbapi.connect(host='localhost', port=18080, user='dbt', catalog='memory').cursor()
c.execute(sys.argv[1]); print(c.fetchall())
EOF
  }
  cp "$W/dbt_project.yml" . ; sed -i.bak 's/dp_integration/dp_trino/g' dbt_project.yml; rm -f dbt_project.yml.bak
  cat > profiles.yml <<'EOF'
dp_trino:
  target: dev
  outputs:
    dev: {type: trino, method: none, user: dbt, host: localhost, port: 18080, database: memory, schema: dp_cache_test, threads: 1, http_scheme: http}
EOF
  echo "select * from memory.dp_cache_src.orders" > models/orders.sql
  printf 'version: 2\nmodels:\n  - name: orders\n    config:\n      tags: [data_parity]\n      event_time: order_date\n' > models/_m.yml
  cp "$W/packages.yml" . ; $DBT2 deps >/dev/null 2>&1
  rm -rf dbt_packages/data_parity; cp -R "$W/dbt_packages/data_parity" dbt_packages/data_parity
  tq "drop table if exists memory.dp_cache_c.data_parity_cache" >/dev/null; tq "drop table if exists memory.dp_cache_src.orders" >/dev/null
  tq "create schema if not exists memory.dp_cache_src" >/dev/null; tq "create schema if not exists memory.dp_cache_c" >/dev/null
  tq "create table memory.dp_cache_src.orders as select cast(d as date) as order_date, cast(d_i * 100 + k as bigint) as order_id, cast((k * 1.37 + d_i) as decimal(18,2)) as amount, cast(k * 0.1 + d_i as double) as price_float, case when k = 3 then null else concat('s', cast(k as varchar), ' ') end as status, (k % 2 = 0) as is_paid, cast(date_add('second', k * 3600 + d_i, timestamp '2025-01-01 00:00:00') as timestamp(6)) as created_at, current_timestamp as meta_loaded_at from (select d, date_diff('day', date '2025-01-01', d) as d_i from unnest(sequence(date '2025-01-01', date '2025-03-31', interval '1' day)) as t(d)) cross join unnest(sequence(1, 20)) as u(k)" >/dev/null
  $DBT2 run --target dev >/dev/null 2>&1
  TA="schema: dp_cache_c, reference_date: 2025-03-20"      # cutoff 2025-03-17: 75 closed, 15 open days
  op $DBT2 ref_pass1 dev "" | P > tref.txt
  op $DBT2 data_parity_cache_refresh dev "$TA" | N | grep -q 'DRY RUN' && [ "$(tq "show schemas from memory like 'dp_cache_c'" | grep -c dp_cache_c)" = 1 ] && [ "$(tq "show tables from memory.dp_cache_c" | grep -c data_parity_cache)" = 0 ] && ok "trino (a) dry run creates no table" || bad "trino dry run"
  op $DBT2 data_parity_cache_refresh dev "$TA, write: true" | N | grep -q 'inserted=75' && ok "trino (b) refresh wrote 75 closed partitions" || bad "trino refresh"
  op $DBT2 data_parity_cached_pass1 dev "$TA" > tc.raw; PC < tc.raw | cmp -s - tref.txt && ok "trino (c) cached pass 1 byte-identical to live ($(wc -l < tref.txt) lines)" || bad "trino identity"
  N < tc.raw | sed 's/^/    /'
  op $DBT2 data_parity_cache_refresh dev "$TA, write: true" | N | grep -q 'up to date' && [ "$(tq 'select count(*) from memory.dp_cache_c.data_parity_cache')" = "[[75]]" ] && ok "trino (d) second refresh adds nothing" || bad "trino second refresh"
  # rewrite a closed day (memory connector cannot UPDATE/DELETE: add 20 duplicate-id rows with a changed amount)
  tq "insert into memory.dp_cache_src.orders select order_date, order_id + 1000000, amount + 0.01, price_float, status, is_paid, created_at, meta_loaded_at from memory.dp_cache_src.orders where order_date = date '2025-02-05'" >/dev/null
  $DBT2 run --target dev >/dev/null 2>&1
  op $DBT2 ref_pass1 dev "" | P > tref2.txt
  op $DBT2 data_parity_cached_pass1 dev "$TA" | PC | cmp -s - tref.txt && ! cmp -s tref.txt tref2.txt && ok "trino (e) stale cache silently serves the OLD value of the rewritten day" || bad "trino premise"
  op $DBT2 data_parity_cache_verify dev "$TA, sample: 1000, fail_on_stale: false" | N | grep -E 'VERIFY\|STALE|stale_partitions' | sed 's/^/    /'
  op $DBT2 data_parity_cache_verify dev "$TA, sample: 1000, fail_on_stale: false" | N | grep -q 'VERIFY|STALE|orders|2025-02-05' && ok "trino (e) verify flags 2025-02-05 as STALE" || bad "trino verify"
  # schema change: new column => all cached rows stale, recomputed, newest row wins (append-only: Trino memory has no row DELETE)
  tq "alter table memory.dp_cache_src.orders add column extra varchar" >/dev/null; $DBT2 run --target dev >/dev/null 2>&1
  op $DBT2 ref_pass1 dev "" | P > tref3.txt
  op $DBT2 data_parity_cached_pass1 dev "$TA" > tf.raw; PC < tf.raw | cmp -s - tref3.txt && N < tf.raw | grep -q 'from_cache=0.*live_closed_not_trusted=75' && ok "trino (f) new column: nothing trusted, output identical to live" || bad "trino schema change"
  op $DBT2 data_parity_cache_refresh dev "$TA, write: true" | N | grep -q 'inserted=75 (superseding stale=75, prune=false)' && op $DBT2 data_parity_cached_pass1 dev "$TA" > tf2.raw && PC < tf2.raw | cmp -s - tref3.txt && N < tf2.raw | grep -q 'from_cache=75' \
    && ok "trino (f) refresh appended 75 rows; cached pass 1 identical, 75 from cache (cache now $(tq 'select count(*) from memory.dp_cache_c.data_parity_cache') rows)" || bad "trino refresh after schema change"
  op $DBT2 data_parity_cached_pass1 dev "$TA" --vars '{data_parity_cache_version: "2"}' | N | grep -q 'from_cache=0.*live_closed_not_trusted=75' && ok "trino (f) version bump: nothing trusted" || bad "trino version bump"
  tq "drop table memory.dp_cache_c.data_parity_cache" >/dev/null; tq "drop table memory.dp_cache_src.orders" >/dev/null; tq "drop table memory.dp_cache_test.orders" >/dev/null
fi

echo
echo "RESULT: $PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
