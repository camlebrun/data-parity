# data-parity

A dbt package that proves a table is **identical on two engines** (BigQuery, DuckDB, Trino) **without moving
data**: each engine prints only aggregates of per-row hashes, and the two small outputs are compared.

Work in progress, delivered as a stack of pull requests. This first one adds the portable row hash
(`dbt_package/macros/canonical.sql`, `row_hash.sql`), its conformance test and the integration test bed.
Spec of the hash: [docs/canonical-spec.md](docs/canonical-spec.md).
