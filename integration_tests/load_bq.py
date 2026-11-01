"""Load the (FAKE, generated) reference fixture engine_a into a throw-away BigQuery dataset shaped like prod.

    engine_a.duckdb  mart.orders       -> <project>.<dataset>.fct_orders     (PARTITION MONTH order_date, CLUSTER customer_id)
    engine_a.duckdb  mart.customers    -> <project>.<dataset>.dim_customers
    engine_a.duckdb  mart.events_nopk  -> <project>.<dataset>.fct_events     (PARTITION MONTH order_date, CLUSTER event_type)

Prod conventions (read from the prod project's metadata): fct_/dim_ prefixes, mart_<domain> dataset, EU location,
MONTH partitioning on the date column + clustering. The DDL is explicit: BigQuery never infers the types.
Only the reference side (engine_a) goes to BigQuery; the corrupted copy lives in Trino.

    python integration_tests/load_bq.py --project <your-gcp-project> [--dataset mart_parity_test] [--location EU]
    python integration_tests/load_bq.py --project <your-gcp-project> --drop     # delete the dataset

Needs: python with duckdb, the `bq` CLI authenticated. Cost: ~20 MB stored, free loads, expiry 1 day.
Type mapping (DuckDB -> BigQuery): BIGINT INT64, VARCHAR STRING, DATE DATE, DECIMAL(18,2) NUMERIC(18,2),
DOUBLE FLOAT64, BOOLEAN BOOL, TIMESTAMP TIMESTAMP (read as UTC).
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent

# fixture table -> (bq table, DDL columns, partition/cluster clause)
TABLES = {
    "orders": ("fct_orders", """order_id INT64, customer_id INT64, order_date DATE, amount NUMERIC(18,2),
        price_float FLOAT64, status STRING, is_paid BOOL, created_at TIMESTAMP, meta_loaded_at TIMESTAMP""",
               "PARTITION BY DATE_TRUNC(order_date, MONTH) CLUSTER BY customer_id"),
    "customers": ("dim_customers", """customer_id INT64, name STRING, email STRING, city STRING,
        meta_loaded_at TIMESTAMP""", ""),
    "events_nopk": ("fct_events", """order_date DATE, event_type STRING, user_id INT64, value NUMERIC(18,2),
        meta_loaded_at TIMESTAMP""",
                    "PARTITION BY DATE_TRUNC(order_date, MONTH) CLUSTER BY event_type"),
}
TS_COLS = {"created_at", "meta_loaded_at"}


PROJECT = None  # set in main(): the bq CLI default project can be anything (e.g. a local emulator), so always pass it


def bq(*args, check=True):
    r = subprocess.run(["bq", "--project_id", PROJECT, *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"bq {' '.join(args[:3])} failed:\n{r.stdout}\n{r.stderr}")
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--dataset", default="mart_parity_test")
    ap.add_argument("--location", default="EU")
    ap.add_argument("--expire-days", type=int, default=1, help="default table expiration (safety net)")
    ap.add_argument("--drop", action="store_true", help="delete the dataset (and its tables) and exit")
    a = ap.parse_args()
    global PROJECT
    PROJECT = a.project
    ds = f"{a.project}:{a.dataset}"
    # safety: this script creates and DELETES datasets; it must never be pointed at a real one
    if not a.dataset.startswith("mart_parity_test"):
        raise SystemExit(f"refusing to touch dataset {a.dataset!r}: the name must start with 'mart_parity_test'")

    if a.drop:
        bq("rm", "-r", "-f", "-d", ds)
        print(f"dropped {ds}")
        return

    if bq("show", "--format=none", ds, check=False).returncode == 0:
        raise SystemExit(f"{ds} already exists: run with --drop first (refusing to overwrite)")
    bq("--location", a.location, "mk", "--dataset", "--default_table_expiration", str(a.expire_days * 86400),
       "--description", "data-parity test bed (FAKE data, safe to delete)", ds)

    con = duckdb.connect(str(HERE / "data" / "engine_a.duckdb"), read_only=True)
    con.execute("SET TimeZone='UTC'")
    with tempfile.TemporaryDirectory() as tmp:
        for src, (dst, cols, clause) in TABLES.items():
            q = " ".join(f"CREATE TABLE `{a.project}.{a.dataset}.{dst}` ({cols}) {clause}".split())
            bq("--location", a.location, "query", "--use_legacy_sql=false", q)
            names = [r[0] for r in con.execute(
                "select column_name from information_schema.columns where table_schema='mart' and table_name=? "
                "order by ordinal_position", [src]).fetchall()]
            sel = ", ".join(f'"{n}"::timestamptz as "{n}"' if n in TS_COLS else f'"{n}"' for n in names)
            pq = Path(tmp) / f"{dst}.parquet"
            con.execute(f"COPY (select {sel} from mart.{src}) TO '{pq}' (FORMAT parquet)")
            bq("--location", a.location, "load", "--source_format=PARQUET", f"{ds}.{dst}", str(pq))
            n = bq("--location", a.location, "query", "--use_legacy_sql=false", "--format=csv",
                   f"select count(*) from `{a.project}.{a.dataset}.{dst}`").stdout.split()[-1]
            print(f"{a.project}.{a.dataset}.{dst}: {n} rows")


if __name__ == "__main__":
    main()
