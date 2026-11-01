"""Load the (FAKE, generated) fixture tables of engine_a / engine_b into Trino, to test cross-engine parity.

    engine_a.duckdb  mart.*  ->  memory.dp_a.*
    engine_b.duckdb  mart.*  ->  memory.dp_b.*

Run with a python that has `duckdb` and `trino` (the dbt-core venv does):
    python integration_tests/load_trino.py [--host localhost --port 18080 --user dbt --catalog memory]

Type mapping (DuckDB -> Trino). Anything not listed aborts: a type that cannot be mapped identically
is a finding, not something to coerce silently.
    BIGINT           -> bigint
    VARCHAR          -> varchar
    DATE             -> date
    DECIMAL(p,s)     -> decimal(p,s)
    DOUBLE           -> double          (value written as repr(), round-trips exactly)
    BOOLEAN          -> boolean
    TIMESTAMP        -> timestamp(6)    (DuckDB TIMESTAMP is microsecond naive)
Options (all default to the historical behaviour used by test_reconcile.sh):
    --schema-a / --schema-b   target schemas (default dp_a / dp_b)
    --rename orders=fct_orders  (repeatable) Trino table name for a fixture table
    --timestamp-tz            TIMESTAMP -> timestamp(6) with time zone, values read as UTC (BigQuery TIMESTAMP is UTC)
Row order is not preserved by Trino's memory connector as a guarantee; hashes do not depend on it.
"""
import argparse
import re
from decimal import Decimal
from pathlib import Path

import duckdb
import trino

HERE = Path(__file__).resolve().parent
TABLES = ["orders", "customers", "events_nopk"]
BATCH = 2000


def map_type(t, tz=False):
    t = t.upper()
    if t in ("BIGINT", "VARCHAR", "DATE", "DOUBLE", "BOOLEAN"):
        return t.lower()
    if t == "TIMESTAMP":
        return "timestamp(6) with time zone" if tz else "timestamp(6)"
    m = re.fullmatch(r"DECIMAL\((\d+),(\d+)\)", t)
    if m:
        return f"decimal({m.group(1)},{m.group(2)})"
    raise SystemExit(f"unmapped DuckDB type {t}: cannot load without changing semantics")


def lit(v, t):
    if v is None:
        return "NULL"
    if t == "bigint":
        return str(int(v))
    if t == "varchar":
        return "'" + v.replace("'", "''") + "'"
    if t == "date":
        return f"DATE '{v.isoformat()}'"
    if t == "double":
        return f"DOUBLE '{repr(float(v))}'"
    if t == "boolean":
        return "true" if v else "false"
    if t == "timestamp(6)":
        return "TIMESTAMP '" + v.strftime("%Y-%m-%d %H:%M:%S.%f") + "'"
    if t == "timestamp(6) with time zone":
        return "TIMESTAMP '" + v.strftime("%Y-%m-%d %H:%M:%S.%f") + " UTC'"
    if t.startswith("decimal"):
        p, s = re.fullmatch(r"decimal\((\d+),(\d+)\)", t).groups()
        return f"CAST('{Decimal(v)}' AS decimal({p},{s}))"
    raise SystemExit(f"no literal for {t}")


def run(cur, sql):
    cur.execute(sql)
    return cur.fetchall()


def load(cur, duck_path, schema, rename=None, tz=False):
    rename = rename or {}
    con = duckdb.connect(str(duck_path), read_only=True)
    run(cur, f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    run(cur, f"CREATE SCHEMA {schema}")
    for tbl in TABLES:
        cols = con.execute(
            "select column_name, data_type from information_schema.columns "
            "where table_schema='mart' and table_name=? order by ordinal_position", [tbl]).fetchall()
        tcols = [(n, map_type(t, tz)) for n, t in cols]
        out = rename.get(tbl, tbl)
        run(cur, f"CREATE TABLE {schema}.{out} (" + ", ".join(f'"{n}" {t}' for n, t in tcols) + ")")
        res = con.execute(f"select * from mart.{tbl}")
        total = 0
        while True:
            rows = res.fetchmany(BATCH)
            if not rows:
                break
            values = ",".join("(" + ",".join(lit(v, t) for v, (_, t) in zip(r, tcols)) + ")" for r in rows)
            run(cur, f"INSERT INTO {schema}.{out} VALUES {values}")
            total += len(rows)
        n = run(cur, f"SELECT count(*) FROM {schema}.{out}")[0][0]
        assert n == total, (tbl, n, total)
        print(f"{schema}.{out}: {n} rows, types " + ", ".join(f"{n_}:{t}" for n_, t in tcols))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=18080)
    ap.add_argument("--user", default="dbt")
    ap.add_argument("--catalog", default="memory")
    ap.add_argument("--schema-a", default="dp_a")
    ap.add_argument("--schema-b", default="dp_b")
    ap.add_argument("--rename", action="append", default=[], metavar="OLD=NEW")
    ap.add_argument("--timestamp-tz", action="store_true")
    a = ap.parse_args()
    rename = dict(r.split("=", 1) for r in a.rename)
    if any(k not in TABLES for k in rename):
        raise SystemExit(f"--rename: unknown fixture table in {a.rename} (known: {TABLES})")
    conn = trino.dbapi.connect(host=a.host, port=a.port, user=a.user, catalog=a.catalog)
    cur = conn.cursor()
    load(cur, HERE / "data" / "engine_a.duckdb", a.schema_a, rename, a.timestamp_tz)
    load(cur, HERE / "data" / "engine_b.duckdb", a.schema_b, rename, a.timestamp_tz)


if __name__ == "__main__":
    main()
