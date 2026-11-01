#!/bin/bash
# End-to-end test on REAL BigQuery vs Trino: the dbt BQ project is verified against the Trino tables.
#   BQ_PROJECT=<gcp project> DBT_CORE=<dbt-core with bigquery+trino adapters> PY=<python with duckdb+trino> \
#     bash integration_tests/test_bq_trino.sh
# Env: BQ_PROJECT  required; the throw-away dataset mart_parity_test is created there (EU, expires in 1 day).
#                  NOTHING IS DELETED by default: if the dataset already exists the script stops.
#                  REPLACE=1 drops and recreates it first; CLEANUP=1 drops it at the end (both need the owner's go).
#                  No emulator: real BigQuery, ~0 EUR (20 MB, free loads).
#      DBT_CORE    required; a dbt-core binary that has both dbt-bigquery and dbt-trino
#      PY          python with duckdb + trino (default python3)
#      TRINO_PORT  default 18080; Trino needs a catalog `local` (memory connector), see README_bq_trino.md
# Needs data/engine_*.duckdb (python integration_tests/generate.py) and `gcloud auth application-default login`.
# Ground truth: expected_findings.md (same data as test_reconcile.sh, tables renamed fct_orders/dim_customers/fct_events).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(dirname "$HERE")
PY=${PY:-python3}
: "${BQ_PROJECT:?set BQ_PROJECT}" "${DBT_CORE:?set DBT_CORE}"
DS=mart_parity_test
W=$(mktemp -d)
FAIL=0
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" BQ_PROJECT BQ_DATASET=$DS

check() { d=$1; shift; if "$@" >/dev/null 2>&1; then echo "  ok   $d"; else echo "  FAIL $d"; FAIL=1; fi; }
has() { [ -s "$1" ] && grep -q -- "$2" "$1"; }
pj() { "$PY" -c 'import json,sys
j = json.load(open(sys.argv[1], encoding="utf-8"))
sys.exit(0 if eval(sys.argv[2]) else 1)' "$1" "$2"; }
M='{m["model"]: m for m in j["models"]}'

cleanup() { [ "${CLEANUP:-0}" = 1 ] && "$PY" "$HERE/load_bq.py" --project "$BQ_PROJECT" --dataset "$DS" --drop >/dev/null 2>&1; }
trap cleanup EXIT

echo "== Trino: load local.$DS (A) and local.${DS}_b (corrupted B), timestamps as UTC"
"$PY" "$HERE/load_trino.py" --port "${TRINO_PORT:-18080}" --catalog local --schema-a $DS --schema-b ${DS}_b \
  --rename orders=fct_orders --rename customers=dim_customers --rename events_nopk=fct_events --timestamp-tz >"$W/load_trino.log" 2>&1 \
  || { echo "  FAIL load_trino (see $W/load_trino.log)"; exit 2; }
echo "== BigQuery: create $BQ_PROJECT.$DS, prod-shaped DDL, load engine_a"
[ "${REPLACE:-0}" = 1 ] && "$PY" "$HERE/load_bq.py" --project "$BQ_PROJECT" --dataset $DS --drop >/dev/null 2>&1
"$PY" "$HERE/load_bq.py" --project "$BQ_PROJECT" --dataset $DS >"$W/load_bq.log" 2>&1 || { echo "  FAIL load_bq (see $W/load_bq.log)"; exit 2; }
check "BQ fct_orders: 200000 rows, partitioned MONTH on order_date, clustered customer_id" \
  bash -c "bq --project_id $BQ_PROJECT show --format=prettyjson $BQ_PROJECT:$DS.fct_orders | grep -q '\"type\": \"MONTH\"' && grep -q 'fct_orders: 200000 rows' $W/load_bq.log"

echo "== dbt: build the views on both sides"
( cd "$HERE/bq_project" && $DBT_CORE deps --profiles-dir . && $DBT_CORE run --profiles-dir . ) >"$W/dbt_bq.log" 2>&1 || { echo "  FAIL dbt run BQ (see $W/dbt_bq.log)"; exit 2; }
( cd "$HERE/trino_local_project" && $DBT_CORE deps --profiles-dir . && $DBT_CORE run --target trino_a --profiles-dir . && $DBT_CORE run --target trino_b --profiles-dir . ) >"$W/dbt_trino.log" 2>&1 || { echo "  FAIL dbt run Trino (see $W/dbt_trino.log)"; exit 2; }

ARGS="--project $HERE/bq_project --profiles-dir-a $HERE/bq_project --project-b $HERE/trino_local_project --profiles-dir-b $HERE/trino_local_project --dbt-a $DBT_CORE --dbt-b $DBT_CORE --a bq"
run_cli() { l=$1; shift; "$PY" -m data_parity run $ARGS "$@" --out "$W/out_$l" --report "$W/$l.md" --json "$W/$l.json" >"$W/$l.log" 2>&1; RC=$?; }
rc() { if [ "$RC" -eq "$2" ]; then echo "  ok   $1 (exit $RC)"; else echo "  FAIL $1 (exit $RC, expected $2)"; FAIL=1; fi; }

echo "== conformance: the hash is portable BigQuery <-> Trino (types, NUMERIC, FLOAT64, UTC timestamps)"
"$PY" -m data_parity conformance $ARGS --b trino_a >"$W/conf.log" 2>&1; RC=$?
rc "conformance bq vs trino" 0
check "conformance says IDENTICAL" has "$W/conf.log" "IDENTICAL"

echo "== BigQuery vs Trino A (same data): must be ISO"
run_cli iso --b trino_a
rc "bq vs trino_a" 0
check "verdict identical, 3 models, not scope limited" pj "$W/iso.json" 'j["verdict"] == "identical" and j["models_total"] == 3 and j["scope_limited"] is False'

echo "== BigQuery vs Trino B (corrupted copy): exactly the findings of expected_findings.md"
run_cli diff --b trino_b
rc "bq vs trino_b" 1
j=$W/diff.json
check "fct_orders: 5 of 120 partitions differ" pj "$j" "$M"'["fct_orders"]["partitions_differing"] == 5 and '"$M"'["fct_orders"]["partitions_total"] == 120'
check "fct_orders: exactly 01-11 01-21 01-31 02-20 03-02 (not the noise day 02-10 nor the reordered day 03-12)" pj "$j" '[p["partition"] for p in '"$M"'["fct_orders"]["differing_partitions"]] == ["2025-01-11", "2025-01-21", "2025-01-31", "2025-02-20", "2025-03-02"]'
check "fct_orders 01-11 missing rows (1667 vs 1664)" pj "$j" '[(p["kind"], p["rows_a"], p["rows_b"]) for p in '"$M"'["fct_orders"]["differing_partitions"] if p["partition"] == "2025-01-11"] == [("rows", 1667, 1664)]'
check "fct_orders 03-02 extra row (1667 vs 1668)" pj "$j" '[(p["kind"], p["rows_a"], p["rows_b"]) for p in '"$M"'["fct_orders"]["differing_partitions"] if p["partition"] == "2025-03-02"] == [("rows", 1667, 1668)]'
check "fct_orders 01-21 amount / 01-31 status / 02-20 price_float" pj "$j" '[(p["partition"], p["differing_columns"]) for p in '"$M"'["fct_orders"]["differing_partitions"] if p["kind"] == "hash"] == [("2025-01-21", ["amount"]), ("2025-01-31", ["status"]), ("2025-02-20", ["price_float"])]'
check "dim_customers: column city" pj "$j" '[c["column"] for c in '"$M"'["dim_customers"]["columns"]] == ["city"]'
check "fct_events: 2025-01-06 -> value" pj "$j" '[(p["partition"], p["differing_columns"]) for p in '"$M"'["fct_events"]["differing_partitions"]] == [("2025-01-06", ["value"])]'
check "meta_loaded_at never reported" pj "$j" 'all("meta_loaded_at" not in [c["column"] for c in m["columns"]] for m in j["models"])'
check "verdict differences, 3 models differ" pj "$j" 'j["verdict"] == "differences" and j["exit_code"] == 1 and j["models_total"] == 3 and j["models_differing"] == 3'

echo "reports and logs: $W"
[ "$FAIL" -eq 0 ] && echo "ALL CHECKS PASSED" || echo "SOME CHECKS FAILED"
exit "$FAIL"
