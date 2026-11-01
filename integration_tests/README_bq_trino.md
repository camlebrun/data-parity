# Test BigQuery (réel) contre Trino

Vérifie le code dbt d'un projet **BigQuery réel** contre des tables **Trino** (`local.<dataset>.<table>`), aux noms et au
schéma façon prod. Pas d'émulateur. Données FAKE (celles de `generate.py`), vérité terrain : `expected_findings.md`.

| Côté | Où | Tables |
|---|---|---|
| A (BQ) | `<BQ_PROJECT>.mart_parity_test` (EU, expire à 1 jour) | `fct_orders`, `dim_customers`, `fct_events` |
| B (Trino) | `local.mart_parity_test` (copie de référence) et `local.mart_parity_test_b` (copie corrompue) | mêmes noms |

Forme prod côté BQ (relevée sur les métadonnées du projet de prod) : préfixes `fct_`/`dim_`, DDL explicite
(`INT64`, `NUMERIC(18,2)`, `FLOAT64`, `TIMESTAMP` UTC, `BOOL`), partition `MONTH` sur `order_date`, clustering
`customer_id` / `event_type`. Côté Trino : `timestamp(6) with time zone` (équivalent du `TIMESTAMP` BQ). Le connecteur
`memory` n'a ni partition ni clustering : limite documentée, non testable ici.

## Prérequis (une fois)

1. `python integration_tests/generate.py` (crée `data/engine_*.duckdb`).
2. Catalogue Trino `local` dans le conteneur `dp-trino` : `docker exec dp-trino sh -c 'echo connector.name=memory > /etc/trino/catalog/local.properties'` puis `docker restart dp-trino`.
3. `gcloud auth application-default login` ; le CLI `bq` doit être authentifié (le script passe `--project_id` explicitement).
4. Un dbt-core avec `dbt-bigquery` et `dbt-trino`, et un python avec `duckdb` + `trino`.

## Lancer

    BQ_PROJECT=<projet> DBT_CORE=<dbt-core> PY=<python> bash integration_tests/test_bq_trino.sh

Le script **ne supprime rien** : si le dataset existe déjà, il s'arrête. `REPLACE=1` le supprime puis le recrée,
`CLEANUP=1` le supprime à la fin. `load_bq.py` refuse tout dataset dont le nom ne commence pas par `mart_parity_test`.
Coût : environ 0 € (20 Mo stockés, chargements gratuits, lectures < 1 Go par run).

## Contrôles

Conformité (hash portable BQ ↔ Trino) ; BQ vs Trino de référence = ISO (3 modèles) ; BQ vs Trino corrompu = exactement
les 5 jours de `fct_orders`, `city` sur `dim_customers`, le jour 2025-01-06 sur `fct_events`, rien sur `meta_loaded_at`.
