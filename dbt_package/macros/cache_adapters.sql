{#
  Per-adapter pieces of the closed-partition cache (see cache.sql, docs/cache.md).
  Only what really differs between engines lives here; everything else is plain SQL.

    DuckDB   : tested
    Trino    : tested on the `memory` connector (default__ implementation)
    BigQuery : UNTESTED, never executed by the author. Written from the docs only.
#}

{# Fully qualified name of the cache table. `schema` is a plain identifier (no dot). #}
{% macro data_parity_cache_fqn(schema) %}
  {% do data_parity_guard_target() %}
  {{ return(adapter.dispatch('data_parity_cache_fqn', 'data_parity')(schema)) }}
{% endmacro %}
{% macro default__data_parity_cache_fqn(schema) %}{{ return('"' ~ target.database ~ '"."' ~ schema ~ '"."data_parity_cache"') }}{% endmacro %}
{% macro bigquery__data_parity_cache_fqn(schema) %}{{ return('`' ~ target.database ~ '`.`' ~ schema ~ '`.`data_parity_cache`') }}{% endmacro %}

{# Column types of the cache table. #}
{% macro data_parity_cache_types() %}
  {% do data_parity_guard_target() %}
  {{ return(adapter.dispatch('data_parity_cache_types', 'data_parity')()) }}
{% endmacro %}
{% macro default__data_parity_cache_types() %}{{ return({'str': 'varchar', 'int': 'bigint', 'ts': 'timestamp(6) with time zone'}) }}{% endmacro %}
{% macro duckdb__data_parity_cache_types() %}{{ return({'str': 'varchar', 'int': 'bigint', 'ts': 'timestamptz'}) }}{% endmacro %}
{% macro bigquery__data_parity_cache_types() %}{{ return({'str': 'string', 'int': 'int64', 'ts': 'timestamp'}) }}{% endmacro %}

{# Create the cache schema if needed. Only ever called when write=true.
   BigQuery: the dataset must already exist (its location is a decision for a human). #}
{% macro data_parity_cache_create_schema(schema) %}
  {% do data_parity_guard_target() %}
  {{ return(adapter.dispatch('data_parity_cache_create_schema', 'data_parity')(schema)) }}
{% endmacro %}
{% macro default__data_parity_cache_create_schema(schema) %}
  {% do run_query('create schema if not exists "' ~ target.database ~ '"."' ~ schema ~ '"') %}
{% endmacro %}
{% macro bigquery__data_parity_cache_create_schema(schema) %}
  {# UNTESTED. Deliberately a no-op: a dataset's location/ACL is a human decision.
     If the dataset does not exist the create table below fails with a clear BigQuery error. #}
{% endmacro %}

{% macro data_parity_cache_create_table(schema) %}
  {% set t = data_parity_cache_types() %}
  {% do run_query(
      'create table if not exists ' ~ data_parity_cache_fqn(schema) ~ ' ('
      ~ 'model ' ~ t['str'] ~ ', partition_value ' ~ t['str']
      ~ ', rows_n ' ~ t['int'] ~ ', h1_sum ' ~ t['int'] ~ ', h2_sum ' ~ t['int']
      ~ ', schema_fp ' ~ t['str'] ~ ', version ' ~ t['str'] ~ ', computed_at ' ~ t['ts'] ~ ')') %}
{% endmacro %}

{# date_sub: SQL date expression `base` minus n days, as a date. #}
{% macro data_parity_cache_date_sub(base, n) %}
  {% do data_parity_guard_target() %}
  {{ return(adapter.dispatch('data_parity_cache_date_sub', 'data_parity')(base, n)) }}
{% endmacro %}
{% macro default__data_parity_cache_date_sub(base, n) %}date_add('day', -{{ n }}, {{ base }}){% endmacro %}
{% macro duckdb__data_parity_cache_date_sub(base, n) %}cast({{ base }} - {{ n }} as date){% endmacro %}
{% macro bigquery__data_parity_cache_date_sub(base, n) %}date_sub({{ base }}, interval {{ n }} day){% endmacro %}

{% macro data_parity_cache_random() %}
  {% do data_parity_guard_target() %}
  {{ return(adapter.dispatch('data_parity_cache_random', 'data_parity')()) }}
{% endmacro %}
{% macro default__data_parity_cache_random() %}random(){% endmacro %}
{% macro bigquery__data_parity_cache_random() %}rand(){% endmacro %}

{# Commit after DML where the adapter needs it (no-op elsewhere). #}
{% macro data_parity_cache_commit() %}
  {% if adapter.commit is defined %}{% do adapter.commit() %}{% endif %}
{% endmacro %}
