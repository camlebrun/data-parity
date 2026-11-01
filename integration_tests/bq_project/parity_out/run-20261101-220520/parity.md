# Parity report: BigQuery vs Trino

> ❌ **Differences found**: 3 of 3 models differ.

| | |
|---|---|
| Date | 2026-11-01 22:02 |
| Engine A | `BigQuery` (dbt target `bq`) |
| Engine B | `Trino` (dbt target `trino_b`) |
| Compared | row counts and a fingerprint of every row, per partition, then per column (no data leaves the warehouse) |
| Scope | whole tables |
| Models | every tagged model (3) |

## Overview

| model | verdict | rows BigQuery | rows Trino | Δ rows | partitions differing | main columns |
|---|---|--:|--:|--:|---|---|
| `dim_customers` | ❌ differs | 5,000 | 5,000 | 0 | table-level | `city` |
| `fct_events` | ❌ differs | 20,010 | 20,010 | 0 | 1/120 (0.8%) | `value` |
| `fct_orders` | ❌ differs | 200,000 | 199,998 | -2 | 5/120 (4.2%) | `amount`, `price_float`, `status` |

## Differences

### ❌ `dim_customers`

> Values differ in `city` (no partition column, so the whole table was compared).

- rows: BigQuery = 5,000, Trino = 5,000 (Δ 0)
- primary key: `customer_id`

**To inspect**: compare table `dim_customers` on BigQuery and Trino, matching rows on `customer_id`. Look at the column `city`.

**Differing columns** (ranked)

| column | buckets affected | share of differing buckets |
|---|--:|--:|
| `city` | 1 | 100% |

### ❌ `fct_events`

> Values changed in 1 of 120 days, all in `value` (1 of 1 day); column attribution covers all 1 differing day.

- rows: BigQuery = 20,010, Trino = 20,010 (Δ 0)
- primary key: none
- partitions differing: **1 of 120** (0.8%)

**To inspect**: compare table `fct_events` on BigQuery and Trino for the day `2025-01-06`, comparing whole rows. Look at the column `value`.

**Differing columns** (ranked). Shares are relative to all 1 differing day with changed values.

| column | days affected | share of differing days |
|---|--:|--:|
| `value` | 1 | 100% |

**Nature of the differences**

| nature | days |
|---|--:|
| changed values (same row count) | 1 |
| missing or extra rows | 0 |


**Differing days**.

| partition | what | rows BigQuery | rows Trino | columns |
|---|---|--:|--:|---|
| 2025-01-06 | same row count, different hashes: changed values | 168 | 168 | `value` |

### ❌ `fct_orders`

> Values changed in 3 of 120 days, over 3 columns (`amount`, `price_float`, `status`), one each (1 of 3 days); missing or extra rows in 2 days (net -2 rows); column attribution covers all 5 differing days.

- rows: BigQuery = 200,000, Trino = 199,998 (Δ -2)
- primary key: `order_id`
- partitions differing: **5 of 120** (4.2%)

**To inspect**: compare table `fct_orders` on BigQuery and Trino for the day `2025-01-11`, matching rows on `order_id`.

**Differing columns** (ranked). Shares are relative to all 3 differing days with changed values.

| column | days affected | share of differing days |
|---|--:|--:|
| `amount` | 1 | 33% |
| `price_float` | 1 | 33% |
| `status` | 1 | 33% |

**Nature of the differences**

| nature | days |
|---|--:|
| changed values (same row count) | 3 |
| missing or extra rows | 2 |

**Timeline**: first differing day `2025-01-11`, last `2025-03-02`.

| period | differing | of | share of the period | |
|---|--:|--:|--:|---|
| 2025-01 | 3 | 31 | 9.7% | ██████████ |
| 2025-02 | 1 | 28 | 3.6% | ████░░░░░░ |
| 2025-03 | 1 | 31 | 3.2% | ███░░░░░░░ |

**Differing days**.

| partition | what | rows BigQuery | rows Trino | columns |
|---|---|--:|--:|---|
| 2025-01-11 | row counts differ: missing or extra rows | 1,667 | 1,664 | n/a (row set differs) |
| 2025-01-21 | same row count, different hashes: changed values | 1,667 | 1,667 | `amount` |
| 2025-01-31 | same row count, different hashes: changed values | 1,667 | 1,667 | `status` |
| 2025-02-20 | same row count, different hashes: changed values | 1,667 | 1,667 | `price_float` |
| 2025-03-02 | row counts differ: missing or extra rows | 1,667 | 1,668 | n/a (row set differs) |

## Not checked

- **Excluded columns** (by configuration, `meta.data_parity`): `dim_customers`: `meta_loaded_at`; `fct_events`: `meta_loaded_at`; `fct_orders`: `meta_loaded_at`.
- **Untagged models**: only models tagged `data_parity` (or with `meta.data_parity`) are part of the comparison.

## How to reproduce

```
data-parity run --a bq --b trino_b --schema-b local.mart_parity_test_b --label-a BigQuery --label-b Trino
```

Tool: data-parity 0.1.0. Exit codes: 0 identical, 1 differences, 2 error.

**Limits.** Identical means identical on what was compared (see the scope and "Not checked"); it does not look at the meaning of the data. A difference this small could in theory go unseen only by a rare hash collision (about 1 in 16 million per change).
