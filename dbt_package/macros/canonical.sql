{#
  Canonical text of a column, identical across engines. See docs/canonical-spec.md.

    data_parity_canonical(col_expr, data_type, scale=6)  -> SQL expression (STRING / VARCHAR)

  NULL is rendered as the sentinel returned by data_parity_null_sentinel().
#}

{# Supported engines only. Called before any adapter-specific SQL is built. #}
{% macro data_parity_guard_target() %}
  {%- if target.type not in ['duckdb', 'bigquery', 'trino'] -%}
    {{ exceptions.raise_compiler_error("data_parity supports duckdb, bigquery and trino only (target.type=" ~ target.type ~ ")") }}
  {%- endif -%}
{% endmacro %}

{# Config value that is a list of non-empty strings: a lone string becomes a one-element list,
   none gives `default`, anything else is a compiler error. `what` names the setting in the message. #}
{% macro data_parity_cfg_list(v, what, default=[]) %}
  {%- if v is none -%}{{ return(default) }}{%- endif -%}
  {%- if v is mapping -%}
    {{ exceptions.raise_compiler_error("data_parity: " ~ what ~ " must be a string or a list of strings, got a mapping") }}
  {%- elif v is string -%}
    {%- set l = [v] -%}
  {%- elif v is sequence -%}
    {%- set l = v | list -%}
  {%- else -%}
    {{ exceptions.raise_compiler_error("data_parity: " ~ what ~ " must be a string or a list of strings, got " ~ (v | string)) }}
  {%- endif -%}
  {%- for x in l -%}
    {%- if x is not string or (x | trim) == '' -%}
      {{ exceptions.raise_compiler_error("data_parity: " ~ what ~ " must contain only non-empty strings, got " ~ (x | string) ~ " (an empty prefix would exclude everything)") }}
    {%- endif -%}
  {%- endfor -%}
  {{- return(l) -}}
{% endmacro %}

{# Strict boolean argument: true/false, or the strings 'true'/'false' (any case). Anything else is an error. #}
{% macro data_parity_cfg_bool(v, what) %}
  {%- if v is boolean -%}{{ return(v) }}{%- endif -%}
  {%- if v is string and (v | trim | lower) in ['true', 'false'] -%}{{ return((v | trim | lower) == 'true') }}{%- endif -%}
  {{ exceptions.raise_compiler_error("data_parity: " ~ what ~ " must be true or false, got " ~ (v | string)) }}
{% endmacro %}

{# true when the model opted out: meta.data_parity: false, or meta.data_parity.enabled: false #}
{% macro data_parity_meta_disabled(meta) %}
  {%- if meta is boolean and not meta -%}{{ return(true) }}{%- endif -%}
  {%- if meta is mapping and meta.get('enabled', true) == false -%}{{ return(true) }}{%- endif -%}
  {{- return(false) -}}
{% endmacro %}

{% macro data_parity_pow10(scale) %}
  {%- set ns = namespace(s='1') -%}
  {%- for i in range(scale) -%}{%- set ns.s = ns.s ~ '0' -%}{%- endfor -%}
  {{- ns.s -}}
{% endmacro %}

{# Type class of a data_type string. `bare_timestamp_is_tz` is true on BigQuery
   (TIMESTAMP is always an instant), false on DuckDB / Trino (TIMESTAMP is naive, read as UTC). #}
{% macro data_parity_type_class(data_type, bare_timestamp_is_tz=false) %}
  {%- set t = (data_type | string | lower | trim) -%}
  {%- set base = t.split('(')[0] | trim -%}
  {%- set has_tz = ('with time zone' in t) or ('timestamptz' in t) or ('timestamp_tz' in t) -%}
  {%- set no_tz = ('without time zone' in t) -%}
  {%- if base in ['boolean', 'bool'] -%}bool
  {%- elif base in ['tinyint', 'smallint', 'int', 'integer', 'bigint', 'int8', 'int16', 'int32', 'int64', 'int2', 'int4', 'byteint', 'hugeint', 'utinyint', 'usmallint', 'uinteger', 'ubigint', 'uhugeint', 'long', 'short'] -%}int
  {%- elif base in ['decimal', 'numeric', 'bignumeric', 'bigdecimal', 'dec', 'number'] -%}decimal
  {%- elif base in ['float', 'float32', 'float64', 'double', 'double precision', 'real'] -%}float
  {%- elif base == 'date' -%}date
  {%- elif base.startswith('timestamp') or base == 'datetime' -%}
    {%- if base == 'datetime' or no_tz -%}ts_naive
    {%- elif has_tz -%}ts_tz
    {%- elif bare_timestamp_is_tz -%}ts_tz
    {%- else -%}ts_naive
    {%- endif -%}
  {%- elif base in ['varchar', 'string', 'char', 'character', 'text', 'character varying', 'bpchar', 'nvarchar'] -%}string
  {%- else -%}unsupported
  {%- endif -%}
{% endmacro %}

{% macro data_parity_unsupported(col_expr, data_type) %}
  {{ exceptions.raise_compiler_error("data_parity: unsupported column type '" ~ data_type ~ "' for column expression " ~ col_expr ~ " (supported: boolean, integers, decimal/numeric, float/double, date, timestamp/datetime, string; array/struct/json/bytes/interval are not portable)") }}
{% endmacro %}

{% macro data_parity_canonical(col_expr, data_type, scale=6) %}
  {% do data_parity_guard_target() %}
  {{ return(adapter.dispatch('data_parity_canonical', 'data_parity')(col_expr, data_type, scale)) }}
{% endmacro %}

{# ------------------------------------------------------------------ Trino / ANSI
   Tested: Trino 484 in Docker (dbt-core 1.12, dbt-trino). Conformance output is byte-identical to DuckDB and BigQuery,
   and the reconcile suite matches DuckDB vs Trino. Not tested: real Trino clusters (other connectors / catalogs). #}
{% macro default__data_parity_canonical(col_expr, data_type, scale=6) %}
  {%- set cls = data_parity_type_class(data_type) | trim -%}
  {%- set p10 = data_parity_pow10(scale) | trim -%}
  {%- if cls == 'bool' -%}
    case when {{ col_expr }} is null then '\N' when {{ col_expr }} then '1' else '0' end
  {%- elif cls == 'int' -%}
    coalesce(cast({{ col_expr }} as varchar), '\N')
  {%- elif cls == 'decimal' -%}
    coalesce(cast(cast(cast({{ col_expr }} as decimal(38, {{ scale }})) * {{ p10 }} as decimal(38, 0)) as varchar), '\N')
  {%- elif cls == 'float' -%}
    case when {{ col_expr }} is null then '\N'
         when is_nan(cast({{ col_expr }} as double)) then 'NaN'
         when is_infinite(cast({{ col_expr }} as double)) and cast({{ col_expr }} as double) > 0 then 'Inf'
         when is_infinite(cast({{ col_expr }} as double)) then '-Inf'
         else cast(cast(round(cast({{ col_expr }} as double) * {{ p10 }}e0) as decimal(38, 0)) as varchar) end
  {%- elif cls == 'date' -%}
    coalesce(cast({{ col_expr }} as varchar), '\N')
  {%- elif cls == 'ts_tz' or cls == 'ts_naive' -%}
    {%- if cls == 'ts_tz' -%}
      {%- set t6 = "cast(" ~ col_expr ~ " at time zone 'UTC' as timestamp(6))" -%}
    {%- else -%}
      {%- set t6 = "cast(" ~ col_expr ~ " as timestamp(6))" -%}
    {%- endif -%}
    case when {{ col_expr }} is null then '\N'
         else cast(date_diff('second', timestamp '1970-01-01 00:00:00', date_trunc('second', {{ t6 }})) * 1000000
                   + cast(substr(cast({{ t6 }} as varchar), 21, 6) as bigint) as varchar) end
  {%- elif cls == 'string' -%}
    coalesce(cast({{ col_expr }} as varchar), '\N')
  {%- else -%}
    {{ data_parity_unsupported(col_expr, data_type) }}
  {%- endif -%}
{% endmacro %}

{# ------------------------------------------------------------------ DuckDB #}
{% macro duckdb__data_parity_canonical(col_expr, data_type, scale=6) %}
  {%- set cls = data_parity_type_class(data_type) | trim -%}
  {%- set p10 = data_parity_pow10(scale) | trim -%}
  {%- if cls == 'bool' -%}
    case when {{ col_expr }} is null then '\N' when {{ col_expr }} then '1' else '0' end
  {%- elif cls == 'int' -%}
    coalesce(cast({{ col_expr }} as varchar), '\N')
  {%- elif cls == 'decimal' -%}
    coalesce(cast(cast(round({{ col_expr }}, {{ scale }}) * {{ p10 }} as hugeint) as varchar), '\N')
  {%- elif cls == 'float' -%}
    case when {{ col_expr }} is null then '\N'
         when isnan({{ col_expr }}) then 'NaN'
         when isinf({{ col_expr }}) and {{ col_expr }} > 0 then 'Inf'
         when isinf({{ col_expr }}) then '-Inf'
         else cast(cast(round(cast({{ col_expr }} as double) * {{ p10 }}e0) as hugeint) as varchar) end
  {%- elif cls == 'date' -%}
    coalesce(strftime({{ col_expr }}, '%Y-%m-%d'), '\N')
  {%- elif cls == 'ts_tz' or cls == 'ts_naive' -%}
    coalesce(cast(epoch_us({{ col_expr }}) as varchar), '\N')
  {%- elif cls == 'string' -%}
    coalesce(cast({{ col_expr }} as varchar), '\N')
  {%- else -%}
    {{ data_parity_unsupported(col_expr, data_type) }}
  {%- endif -%}
{% endmacro %}

{# ------------------------------------------------------------------ BigQuery #}
{# '\\N' because BigQuery string literals process backslash escapes. #}
{% macro bigquery__data_parity_canonical(col_expr, data_type, scale=6) %}
  {%- set cls = data_parity_type_class(data_type, true) | trim -%}
  {%- set p10 = data_parity_pow10(scale) | trim -%}
  {%- if cls == 'bool' -%}
    case when {{ col_expr }} is null then '\\N' when {{ col_expr }} then '1' else '0' end
  {%- elif cls == 'int' -%}
    coalesce(cast({{ col_expr }} as string), '\\N')
  {%- elif cls == 'decimal' -%}
    coalesce(cast(round(cast({{ col_expr }} as bignumeric), {{ scale }}) * {{ p10 }} as string), '\\N')
  {%- elif cls == 'float' -%}
    case when {{ col_expr }} is null then '\\N'
         when is_nan({{ col_expr }}) then 'NaN'
         when is_inf({{ col_expr }}) and {{ col_expr }} > 0 then 'Inf'
         when is_inf({{ col_expr }}) then '-Inf'
         else cast(cast(round(cast({{ col_expr }} as float64) * {{ p10 }}e0) as bignumeric) as string) end
  {%- elif cls == 'date' -%}
    coalesce(concat(lpad(cast(extract(year from {{ col_expr }}) as string), 4, '0'), format_date('-%m-%d', {{ col_expr }})), '\\N')
  {%- elif cls == 'ts_tz' -%}
    coalesce(cast(unix_micros({{ col_expr }}) as string), '\\N')
  {%- elif cls == 'ts_naive' -%}
    coalesce(cast(unix_micros(timestamp({{ col_expr }}, 'UTC')) as string), '\\N')
  {%- elif cls == 'string' -%}
    coalesce(cast({{ col_expr }} as string), '\\N')
  {%- else -%}
    {{ data_parity_unsupported(col_expr, data_type) }}
  {%- endif -%}
{% endmacro %}
