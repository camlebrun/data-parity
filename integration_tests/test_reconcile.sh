#!/bin/sh
# End-to-end test of the Python CLI (python -m data_parity run) against expected_findings.md.
#   sh integration_tests/test_reconcile.sh            (from anywhere)
# Env: PY          python used to run the tool (stdlib only; default python3). For TRINO=1 it must also have
#                  duckdb + trino (load_trino.py), or set CORE_PY for the loader only.
#      DBT_FUSION  dbt Fusion binary (default: dbt)
#      DBT_CORE    dbt-core binary; the dbt-core and Trino scenarios are skipped if unset
#      TRINO=1     run the Trino scenarios (needs the container on localhost:18080)
# Assertions use the exit code and the parity.json written by the tool (robust), plus a few Markdown spot checks.
# Every assertion fails if the file it reads is missing (no vacuous pass).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(dirname "$HERE")
PY=${PY:-python3}
DBT_FUSION=${DBT_FUSION:-dbt}
W=$(mktemp -d)
FAIL=0
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

check() { # description, command...
  d=$1; shift
  if "$@" >/dev/null 2>&1; then echo "  ok   $d"; else echo "  FAIL $d"; FAIL=1; fi
}
# Markdown helpers: both fail when the file is missing or empty.
has() { [ -s "$1" ] && grep -q -- "$2" "$1"; }
hasnt() { [ -s "$1" ] && ! grep -q -- "$2" "$1"; }
# JSON helper: pj FILE 'python expression on j' ; fails if the file is missing/invalid or the expression is false.
pj() {
  "$PY" -c 'import json,sys
j = json.load(open(sys.argv[1], encoding="utf-8"))
sys.exit(0 if eval(sys.argv[2]) else 1)' "$1" "$2"
}
# same models in two result files (labels and dates may differ)
same_models() { # json1 json2
  "$PY" -c 'import json,sys
a, b = [json.load(open(p, encoding="utf-8")) for p in sys.argv[1:3]]
sys.exit(0 if a["models"] and a["models"] == b["models"] else 1)' "$1" "$2"
}

# run_cli LABEL ARGS...  -> $W/LABEL.json, $W/LABEL.md, $W/LABEL.log ; exit code in $RC
run_cli() {
  l=$1; shift
  "$PY" -m data_parity run "$@" --out "$W/out_$l" --report "$W/$l.md" --json "$W/$l.json" >"$W/$l.log" 2>&1
  RC=$?
}
expect_rc() { # description, expected
  if [ "$RC" -eq "$2" ]; then echo "  ok   $1 (exit $RC)"; else echo "  FAIL $1 (exit $RC, expected $2)"; FAIL=1; fi
}

# expectations on a run of engine_a (reference) vs a corrupted copy
expect_findings() { # label
  j=$W/$1.json; r=$W/$1.md
  check "orders: 5 of 120 partitions differ" pj "$j" '{m["model"]: m for m in j["models"]}["orders"]["partitions_differing"] == 5 and {m["model"]: m for m in j["models"]}["orders"]["partitions_total"] == 120'
  check "orders: exactly 2025-01-11 01-21 01-31 02-20 03-02 (not 02-10, not 03-12)" pj "$j" '[p["partition"] for p in {m["model"]: m for m in j["models"]}["orders"]["differing_partitions"]] == ["2025-01-11", "2025-01-21", "2025-01-31", "2025-02-20", "2025-03-02"]'
  check "orders 01-11 is missing rows (1667 vs 1664)" pj "$j" '[(p["kind"], p["rows_a"], p["rows_b"]) for p in {m["model"]: m for m in j["models"]}["orders"]["differing_partitions"] if p["partition"] == "2025-01-11"] == [("rows", 1667, 1664)]'
  check "orders 03-02 has an extra row (1667 vs 1668)" pj "$j" '[(p["kind"], p["rows_a"], p["rows_b"]) for p in {m["model"]: m for m in j["models"]}["orders"]["differing_partitions"] if p["partition"] == "2025-03-02"] == [("rows", 1667, 1668)]'
  check "orders 01-21 -> hash, amount" pj "$j" '[(p["kind"], p["differing_columns"]) for p in {m["model"]: m for m in j["models"]}["orders"]["differing_partitions"] if p["partition"] == "2025-01-21"] == [("hash", ["amount"])]'
  check "orders 01-31 -> hash, status" pj "$j" '[(p["kind"], p["differing_columns"]) for p in {m["model"]: m for m in j["models"]}["orders"]["differing_partitions"] if p["partition"] == "2025-01-31"] == [("hash", ["status"])]'
  check "orders 02-20 -> hash, price_float" pj "$j" '[(p["kind"], p["differing_columns"]) for p in {m["model"]: m for m in j["models"]}["orders"]["differing_partitions"] if p["partition"] == "2025-02-20"] == [("hash", ["price_float"])]'
  check "meta_loaded_at never named as a differing column" pj "$j" 'len(j["models"]) == 3 and all("meta_loaded_at" not in [c["column"] for c in m["columns"]] and all("meta_loaded_at" not in p["differing_columns"] for p in m["differing_partitions"]) for m in j["models"])'
  check "customers: column city (table-level)" pj "$j" '[c["column"] for c in {m["model"]: m for m in j["models"]}["customers"]["columns"]] == ["city"] and {m["model"]: m for m in j["models"]}["customers"]["verdict"] == "diff"'
  check "events_nopk: 1 of 120 partitions" pj "$j" '{m["model"]: m for m in j["models"]}["events_nopk"]["partitions_differing"] == 1 and {m["model"]: m for m in j["models"]}["events_nopk"]["partitions_total"] == 120'
  check "events_nopk: 2025-01-06 -> value" pj "$j" '[(p["partition"], p["differing_columns"]) for p in {m["model"]: m for m in j["models"]}["events_nopk"]["differing_partitions"]] == [("2025-01-06", ["value"])]'
  check "verdict differences, 3 models, 3 differ, not scope limited" pj "$j" 'j["verdict"] == "differences" and j["exit_code"] == 1 and j["models_total"] == 3 and j["models_differing"] == 3 and j["scope_limited"] is False'
  check "markdown banner: Differences found" has "$r" "Differences found"
  check "markdown has the 'Not checked' section" has "$r" "^## Not checked"
  check "markdown: 2025-02-10 and 2025-03-12 not flagged" hasnt "$r" "2025-02-10\|2025-03-12"
  check "markdown: 2025-01-11 row listed" has "$r" "^| 2025-01-11 |"
}

echo "== build both DuckDB engines"
( cd "$HERE" && $DBT_FUSION run --target engine_a && $DBT_FUSION run --target engine_b ) >/dev/null 2>&1 || { echo "dbt run failed"; exit 2; }

echo "== Fusion: engine_a vs engine_b"
run_cli fusion --project "$HERE" --a engine_a --b engine_b --dbt-a "$DBT_FUSION" --dbt-b "$DBT_FUSION"
expect_rc "differences found" 1
expect_findings fusion

echo "== iso control: engine_a vs engine_a"
run_cli iso --project "$HERE" --a engine_a --b engine_a --dbt-a "$DBT_FUSION" --dbt-b "$DBT_FUSION"
expect_rc "engine_a vs itself" 0
check "iso: verdict identical, 3 models, not scope limited" pj "$W/iso.json" 'j["verdict"] == "identical" and j["models_total"] == 3 and j["models_differing"] == 0 and j["scope_limited"] is False and j["verdict_text"].startswith("ISO: all 3 models")'
check "iso: markdown banner Identical" has "$W/iso.md" "Identical\*\*: all 3 models match"

echo "== exit codes: errors are 2, never 0/1"
run_cli badtarget --project "$HERE" --a engine_a --b no_such_target --dbt-a "$DBT_FUSION" --dbt-b "$DBT_FUSION"
expect_rc "unknown target on side B" 2
check "bad target: json verdict error" pj "$W/badtarget.json" 'j["verdict"] == "error" and j["exit_code"] == 2 and j["errors"]'
check "bad target: markdown says No verdict" has "$W/badtarget.md" "No verdict"
run_cli typo orders_typo --project "$HERE" --a engine_a --b engine_b --dbt-a "$DBT_FUSION" --dbt-b "$DBT_FUSION"
expect_rc "model that does not exist" 2
check "typo model: json verdict error" pj "$W/typo.json" 'j["verdict"] == "error" and j["exit_code"] == 2 and j["errors"]'

echo "== partial scope is never plain ISO"
run_cli full --project "$HERE" --a engine_a --b engine_b --dbt-a "$DBT_FUSION" --dbt-b "$DBT_FUSION" orders
expect_rc "orders, whole table" 1
check "orders whole: scope_limited false" pj "$W/full.json" 'j["scope_limited"] is False and j["verdict"] == "differences"'
run_cli last5 --project "$HERE" --a engine_a --b engine_b --dbt-a "$DBT_FUSION" --dbt-b "$DBT_FUSION" orders --last 5
expect_rc "orders --last 5 (differences are older)" 0
check "last 5: scope_limited true, identical_scoped" pj "$W/last5.json" 'j["scope_limited"] is True and j["verdict"] == "identical_scoped" and j["options"]["last"] == 5'
check "last 5: verdict text is 'ISO on the checked scope'" pj "$W/last5.json" '"ISO on the checked scope" in j["verdict_text"] and not j["verdict_text"].startswith("ISO:")'
check "last 5: markdown banner limited scope" has "$W/last5.md" "Identical on a limited scope"
check "last 5: 'Not checked' names the older partitions" has "$W/last5.md" "Older partitions"

echo "== pass 1 is one grouped scan (compiled SQL)"
( cd "$HERE" && $DBT_FUSION --debug run-operation data_parity_reconcile --target engine_a --args '{only: [orders], level: 1}' ) >"$W/dbg.txt" 2>&1
check "exactly one 'group by dp_p' statement" test "$(grep -c 'group by dp_p' "$W/dbg.txt")" -eq 1

if [ -n "${DBT_CORE:-}" ]; then
  echo "== dbt-core (Jinja2): engine_a vs engine_b"
  run_cli core --project "$HERE" --a engine_a --b engine_b --dbt-a "$DBT_CORE" --dbt-b "$DBT_CORE"
  expect_rc "dbt-core differences found" 1
  expect_findings core
  check "dbt-core result identical to Fusion result (models)" same_models "$W/core.json" "$W/fusion.json"
else
  echo "== dbt-core scenario skipped (set DBT_CORE)"
fi

if [ "${TRINO:-0}" = 1 ] && [ -n "${DBT_CORE:-}" ]; then
  LOADPY=${CORE_PY:-$PY}
  echo "== load Trino (memory.dp_a / dp_b) and build views"
  "$LOADPY" "$HERE/load_trino.py" >"$W/load.log" 2>&1 || { echo "  FAIL load_trino"; FAIL=1; }
  ( cd "$HERE/trino_project" && $DBT_CORE deps --profiles-dir . && $DBT_CORE run --target trino_a --profiles-dir . && $DBT_CORE run --target trino_b --profiles-dir . ) >/dev/null 2>&1 || { echo "  FAIL trino dbt run"; FAIL=1; }
  TRINO_ARGS="--project $HERE --project-b $HERE/trino_project --profiles-dir-b $HERE/trino_project --dbt-a $DBT_FUSION --dbt-b $DBT_CORE"
  echo "== DuckDB engine_a vs Trino dp_a (must be ISO)"
  # shellcheck disable=SC2086
  run_cli trino_iso $TRINO_ARGS --a engine_a --b trino_a
  expect_rc "engine_a vs trino_a" 0
  check "DuckDB engine_a == Trino dp_a (identical, 3 models)" pj "$W/trino_iso.json" 'j["verdict"] == "identical" and j["models_total"] == 3 and j["scope_limited"] is False'
  echo "== DuckDB engine_a vs Trino dp_b (same findings)"
  # shellcheck disable=SC2086
  run_cli trino_b $TRINO_ARGS --a engine_a --b trino_b
  expect_rc "engine_a vs trino_b" 1
  expect_findings trino_b
  check "same findings as DuckDB A vs B (models)" same_models "$W/trino_b.json" "$W/fusion.json"
fi

echo "reports and logs: $W"
[ "$FAIL" -eq 0 ] && echo "ALL CHECKS PASSED" || echo "SOME CHECKS FAILED"
exit "$FAIL"
