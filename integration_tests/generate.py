"""Build the data-parity test bed: two DuckDB files (engine_a = reference,
engine_b = reference + documented corruptions) and expected_findings.md.

Run from anywhere:
    uv run --no-project --with duckdb python integration_tests/generate.py
Deterministic: all values derive from hash(order_id), no RNG, no clock.
"""
import shutil
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
A, B = DATA / "engine_a.duckdb", DATA / "engine_b.duckdb"
D0 = "DATE '2025-01-01'"
N_ORDERS, N_DAYS, N_CUST, N_EVENTS = 200_000, 120, 5_000, 20_000

# day offsets (order_date = 2025-01-01 + offset) used for each corruption
DAY = dict(missing=10, amount=20, status=30, noise=40, real=50, extra=60, reorder=70, events=5)
CUSTOMER_ID = 123


def build_a(con):
    con.execute("create schema mart")
    con.execute(f"""
    create table mart.orders as
    select
      i::bigint as order_id,
      (hash(i) % {N_CUST} + 1)::bigint as customer_id,
      (({D0} + (i % {N_DAYS})::int))::date as order_date,
      ((hash(i * 7) % 1000000) / 100.0)::decimal(18,2) as amount,
      ((hash(i * 11) % 100000) / 37.0)::double as price_float,
      case hash(i * 13) % 10
        when 0 then null when 1 then '' when 2 then 'payé' when 3 then 'paid'
        when 4 then 'annulé' when 5 then 'shipped' else 'pending' end as status,
      (hash(i * 17) % 2 = 0) as is_paid,
      (timestamp '2025-01-01 00:00:00' + to_microseconds((i * 86400000007) % 31536000000000))
        as created_at,
      timestamp '2025-06-01 00:00:00' as meta_loaded_at
    from range(1, {N_ORDERS + 1}) t(i) order by order_id
    """)
    con.execute(f"""
    create table mart.customers as
    select i::bigint as customer_id,
      'Customer ' || i as name,
      'c' || i || '@example.org' as email,
      case when hash(i * 3) % 7 = 0 then null
           else (['Paris','Lyon','Besançon','Zürich','Lille'])[(hash(i) % 5 + 1)::bigint] end as city,
      timestamp '2025-06-01 00:00:00' as meta_loaded_at
    from range(1, {N_CUST + 1}) t(i) order by customer_id
    """)
    # make the corrupted customer deterministic: NULL in A (becomes '' in B)
    con.execute(f"update mart.customers set city = null where customer_id = {CUSTOMER_ID}")
    con.execute(f"""
    create table mart.events_nopk as
    select (({D0} + (i % {N_DAYS})::int))::date as order_date,
      (['click','view','buy','share'])[(hash(i) % 4 + 1)::bigint] as event_type,
      (hash(i * 5) % 900 + 1)::bigint as user_id,
      ((hash(i * 19) % 100000) / 100.0)::decimal(18,2) as value,
      timestamp '2025-06-01 00:00:00' as meta_loaded_at
    from range(1, {N_EVENTS + 1}) t(i) order by i
    """)
    # 10 exact duplicate rows (legit without a pk)
    con.execute("insert into mart.events_nopk select * from mart.events_nopk limit 10")


def one(con, sql):
    return con.execute(sql).fetchone()[0]


def corrupt_b(con):
    f = {}
    day = lambda k: f"order_date = {D0} + {DAY[k]}"
    # a. 3 missing rows
    ids = [r[0] for r in con.execute(f"select order_id from mart.orders where {day('missing')} order by order_id limit 3").fetchall()]
    con.execute(f"delete from mart.orders where order_id in ({','.join(map(str, ids))})")
    f["missing_ids"] = ids
    # b. 2 amounts +0.01
    ids = [r[0] for r in con.execute(f"select order_id from mart.orders where {day('amount')} order by order_id limit 2").fetchall()]
    con.execute(f"update mart.orders set amount = amount + 0.01 where order_id in ({','.join(map(str, ids))})")
    f["amount_ids"] = ids
    # c. one status 'paid' -> 'paid '
    oid = one(con, f"select order_id from mart.orders where {day('status')} and status = 'paid' order by order_id limit 1")
    con.execute(f"update mart.orders set status = 'paid ' where order_id = {oid}")
    f["status_id"] = oid
    # d. float noise (<1e-9 relative) on all rows of day `noise`, real change on 2 rows of day `real`
    f["noise_rows"] = one(con, f"select count(*) from mart.orders where {day('noise')}")
    con.execute(f"update mart.orders set price_float = price_float * (1 + 1e-12) where {day('noise')}")
    ids = [r[0] for r in con.execute(f"select order_id from mart.orders where {day('real')} order by order_id limit 2").fetchall()]
    con.execute(f"update mart.orders set price_float = price_float + 0.01 where order_id in ({','.join(map(str, ids))})")
    f["real_ids"] = ids
    # e. 1 extra row only in B
    xid = N_ORDERS + 1
    con.execute(f"""insert into mart.orders values ({xid}, 42, {D0} + {DAY['extra']}, 12.34, 5.5, 'pending', true,
        timestamp '2025-03-01 10:00:00.123456', timestamp '2025-06-01 00:00:00')""")
    f["extra_id"] = xid
    # f. customers NULL -> ''
    con.execute(f"update mart.customers set city = '' where customer_id = {CUSTOMER_ID}")
    # h. same day, different physical order
    con.execute(f"create temp table d as select * from mart.orders where {day('reorder')}")
    f["reorder_rows"] = one(con, "select count(*) from d")
    con.execute(f"delete from mart.orders where {day('reorder')}")
    con.execute("insert into mart.orders select * from d order by order_id desc")
    # i. events_nopk: one corrupted row
    con.execute(f"""update mart.events_nopk set value = value + 1
        where rowid = (select min(rowid) from mart.events_nopk where order_date = {D0} + {DAY['events']})""")
    f["events_row"] = one(con, f"select count(*) from mart.events_nopk where order_date = {D0} + {DAY['events']}")
    # g. meta_loaded_at differs everywhere
    for t in ("orders", "customers", "events_nopk"):
        con.execute(f"update mart.{t} set meta_loaded_at = timestamp '2025-06-02 12:00:00'")
    return f


def dstr(con, k):
    return str(one(con, f"select {D0} + {DAY[k]}"))


def write_findings(con, f):
    d = {k: dstr(con, k) for k in DAY}
    txt = f"""# Expected findings (ground truth of the test bed)

Generated by `generate.py` (deterministic). engine_a = reference, engine_b = corrupted copy.
A correct reconciliation of `engine_a` vs `engine_b` (schema `mart`) must reproduce exactly these.
Columns whose name starts with `meta_` are excluded by design.

| # | Table | Partition (order_date) | Kind | Exact count | Must be flagged |
|---|-------|------------------------|------|-------------|-----------------|
| a | orders | {d['missing']} | rows missing in B (order_id {f['missing_ids']}) | 3 rows | YES (count A - B = 3) |
| b | orders | {d['amount']} | `amount` +0.01 in B (order_id {f['amount_ids']}) | 2 rows | YES (same count, hash differs) |
| c | orders | {d['status']} | `status` 'paid' vs 'paid ' (order_id {f['status_id']}) | 1 row | YES (trailing space is significant) |
| d1 | orders | {d['noise']} | `price_float` relative noise 1e-12 on all rows of the day | {f['noise_rows']} rows | NO (tolerated by scale-6 canonicalisation) |
| d2 | orders | {d['real']} | `price_float` +0.01 in B (order_id {f['real_ids']}) | 2 rows | YES |
| e | orders | {d['extra']} | extra row only in B (order_id {f['extra_id']}) | 1 row | YES (count B - A = 1) |
| f | customers | (no date) | `city` NULL in A vs '' in B (customer_id {CUSTOMER_ID}) | 1 row | YES (NULL != empty string) |
| g | all 3 tables | all | `meta_loaded_at` differs on every row | every row | NO (meta_ prefix ignored) |
| h | orders | {d['reorder']} | identical rows, inserted in a different physical order | {f['reorder_rows']} rows | NO |
| i | events_nopk | {d['events']} | one row with `value` + 1 (no pk, 10 legit duplicate rows exist in both) | 1 row | YES (day has {f['events_row']} rows in B) |

Summary: orders must report exactly 6 differing partitions (a, b, c, d2, e) = **5** days
({d['missing']}, {d['amount']}, {d['status']}, {d['real']}, {d['extra']}); days {d['noise']} and {d['reorder']} must be identical.
customers: 1 table-level difference. events_nopk: 1 differing day ({d['events']}).
Row counts: orders A = {N_ORDERS}, B = {N_ORDERS - 3 + 1}; customers {N_CUST} / {N_CUST}; events_nopk {N_EVENTS + 10} / {N_EVENTS + 10}.
"""
    txt = txt.replace("exactly 6 differing partitions (a, b, c, d2, e) = **5** days", "exactly **5** differing days (a, b, c, d2, e)")
    (HERE / "expected_findings.md").write_text(txt)


def main():
    DATA.mkdir(exist_ok=True)
    for p in DATA.glob("engine_*.duckdb*"):
        p.unlink()
    con = duckdb.connect(str(A))
    build_a(con)
    con.execute("checkpoint")
    con.close()
    shutil.copy(A, B)
    con = duckdb.connect(str(B))
    f = corrupt_b(con)
    write_findings(con, f)
    con.execute("checkpoint")
    con.close()
    print("ok", A, B)


if __name__ == "__main__":
    main()
