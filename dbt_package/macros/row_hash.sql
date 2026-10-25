{#
  Portable per-row hash, as two small integers (see docs/canonical-spec.md).

    data_parity_row_hash_parts(columns) -> {h1: <sql>, h2: <sql>}
        columns = [{name: 'id', data_type: 'BIGINT'}, ...]

  h1 and h2 are each in [0, 2^24): hex chars 1-6 and 7-12 of md5(row string).
  Per group, count(*), sum(h1), sum(h2) cannot overflow int64 below ~2^39 rows
  and do not depend on row order.

  Row string = '|'-joined pieces of the columns sorted by lower(name):
      NULL value : <name>:N
      otherwise  : <name>:<char length of canonical text>:<canonical text>
  The length prefix makes the join unambiguous without escaping ('|' in strings).
#}

{% macro data_parity_row_string(columns) %}
  {%- do data_parity_guard_target() -%}
  {#- sort by lower(name) explicitly -#}
  {%- set by_name = {} -%}
  {%- for c in columns -%}{%- do by_name.update({(c['name'] | lower): c}) -%}{%- endfor -%}
  {%- set pieces = [] -%}
  {%- for key in (by_name.keys() | list | sort) -%}
    {%- set c = by_name[key] -%}
    {%- set q = adapter.quote(c['name']) -%}
    {%- set canon = data_parity_canonical(q, c['data_type']) | trim -%}
    {%- set lit_name = "'" ~ (c['name'] | lower | replace("'", "''")) -%}
    {%- do pieces.append("case when " ~ q ~ " is null then " ~ lit_name ~ ":N' else " ~ lit_name ~ ":' || cast(length(" ~ canon ~ ") as " ~ data_parity_string_type() ~ ") || ':' || " ~ canon ~ " end") -%}
  {%- endfor -%}
  {{- pieces | join(" || '|' || ") -}}
{% endmacro %}

{% macro data_parity_string_type() %}
  {% do data_parity_guard_target() %}
  {{ return(adapter.dispatch('data_parity_string_type', 'data_parity')()) }}
{% endmacro %}
{% macro default__data_parity_string_type() %}varchar{% endmacro %}
{% macro bigquery__data_parity_string_type() %}string{% endmacro %}

{# md5 of a string expression -> int in [0, 2^24), taking hex chars [start, start+6). #}
{% macro data_parity_hash_int(str_expr, start) %}
  {% do data_parity_guard_target() %}
  {{ return(adapter.dispatch('data_parity_hash_int', 'data_parity')(str_expr, start)) }}
{% endmacro %}

{# Trino (tested on Trino 484 in Docker, dbt-core 1.12; not on real clusters): to_hex is upper case, from_base accepts both. #}
{% macro default__data_parity_hash_int(str_expr, start) %}
  from_base(substr(lower(to_hex(md5(to_utf8({{ str_expr }})))), {{ start }}, 6), 16)
{% endmacro %}

{% macro duckdb__data_parity_hash_int(str_expr, start) %}
  cast(('0x' || substr(md5({{ str_expr }}), {{ start }}, 6)) as bigint)
{% endmacro %}

{% macro bigquery__data_parity_hash_int(str_expr, start) %}
  cast(concat('0x', substr(to_hex(md5({{ str_expr }})), {{ start }}, 6)) as int64)
{% endmacro %}

{% macro data_parity_row_hash_parts(columns) %}
  {%- do data_parity_guard_target() -%}
  {%- set s = data_parity_row_string(columns) | trim -%}
  {{ return({'h1': data_parity_hash_int(s, 1) | trim, 'h2': data_parity_hash_int(s, 7) | trim}) }}
{% endmacro %}
