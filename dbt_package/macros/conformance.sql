{#
  Cross-engine conformance test of the canonical form and the row hash.

    dbt run-operation data_parity_conformance | sed -n 's/.*PARITY|//p' > engine_a.txt
    (same on the other engine)  ->  diff engine_a.txt engine_b.txt   must be EMPTY.

  Builds an inline synthetic dataset (select ... union all select ...): no table
  is read or created. One output line per synthetic row id, sorted by id:
    PARITY|<id>|s=..|i=..|d=..|f=..|dt=..|ts=..|tn=..|b=..|h1=..|h2=..
#}

{% macro data_parity_conformance_types() %}
  {%- if target.type == 'duckdb' -%}
    {{ return({'s': 'varchar', 'i': 'bigint', 'd': 'decimal(38,9)', 'f': 'double', 'dt': 'date', 'ts': 'timestamptz', 'tn': 'timestamp', 'b': 'boolean'}) }}
  {%- elif target.type == 'bigquery' -%}
    {{ return({'s': 'string', 'i': 'int64', 'd': 'numeric', 'f': 'float64', 'dt': 'date', 'ts': 'timestamp', 'tn': 'datetime', 'b': 'bool'}) }}
  {%- else -%}
    {{ return({'s': 'varchar', 'i': 'bigint', 'd': 'decimal(38,9)', 'f': 'double', 'dt': 'date', 'ts': 'timestamp(6) with time zone', 'tn': 'timestamp(6)', 'b': 'boolean'}) }}
  {%- endif -%}
{% endmacro %}

{# NOT included (known non-portable, see docs/canonical-spec.md): floats with |x*10^scale| >= 2^53 (e.g. 1e22: Trino rounds to the shortest decimal, DuckDB/BigQuery keep the exact double), because the engines then differ. #}
{# Strings are given as SQL literals; non-ASCII characters are written literally. #}
{% macro data_parity_conformance_cases() %}
  {{ return({
    's': ["''", "' '", "'x '", "' x'", "'x'", "'a|b'", "'a||b'", "'N'", "'NULL'", "'0'", "'é'", "'é'", "'Straße'", "'日本語'", "'😀'", "'👨‍👩‍👧'", "'Ω'", "'a\"b'", "'1:x'"],
    'i': ["0", "-1", "1", "9007199254740993", "-9007199254740993", "9223372036854775807", "-9223372036854775808", "2147483648"],
    'd': ["0", "0.1", "0.3", "-0.0000005", "0.0000005", "0.0000004999", "-0.0000004999", "1000000000000000", "-123.456789012", "99999999999999999999.999999999", "0.000001", "1.0000015", "2.5"],
    'f': ["0.1", "0.30000000000000004", "-0.0000005", "0.0000005", "0.00000025", "1000000000000000", "1e-7", "-0.0", "'NaN'", "'Infinity'", "'-Infinity'", "1.5e-6", "2.5e-6", "123456789.123456789", "9007199254740993"],
    'dt': ["'1970-01-01'", "'1969-12-31'", "'9999-12-31'", "'0001-01-01'", "'2024-02-29'", "'2026-10-25'"],
    'ts': ["'2026-03-01 12:34:56.123456+02:00'", "'1970-01-01 00:00:00+00:00'", "'1969-12-31 23:59:59.999999+00:00'", "'9999-12-31 23:59:59.999999+00:00'", "'2026-01-01 00:00:00.000001+00:00'", "'2026-06-15 23:30:00-07:00'", "'1900-01-01 00:00:00.5+00:00'"],
    'tn': ["'2026-03-01 12:34:56.123456'", "'1970-01-01 00:00:00'", "'1969-12-31 23:59:59.999999'", "'9999-12-31 23:59:59.999999'"],
    'b': ["true", "false"]
  }) }}
{% endmacro %}

{% macro data_parity_conformance_lit(col, lit, types) %}
  {%- if lit is none -%}
    cast(null as {{ types[col] }})
  {%- elif col in ['s', 'dt', 'ts', 'tn', 'f'] and lit.startswith("'") -%}
    cast({{ lit }} as {{ types[col] }})
  {%- elif col == 'f' -%}
    cast('{{ lit }}' as {{ types[col] }})
  {%- elif col in ['i', 'd'] -%}
    cast('{{ lit }}' as {{ types[col] }})
  {%- else -%}
    cast({{ lit }} as {{ types[col] }})
  {%- endif -%}
{% endmacro %}

{% macro data_parity_conformance() %}
  {%- set types = data_parity_conformance_types() -%}
  {%- set cases = data_parity_conformance_cases() -%}
  {%- set cols = ['s', 'i', 'd', 'f', 'dt', 'ts', 'tn', 'b'] -%}
  {%- set rows = [] -%}
  {#- row 0: all NULL -#}
  {%- set r0 = [] -%}
  {%- for c in cols -%}{%- do r0.append(data_parity_conformance_lit(c, none, types) | trim ~ ' as ' ~ c) -%}{%- endfor -%}
  {%- do rows.append(r0) -%}
  {#- one row per (column, value), other columns NULL -#}
  {%- for c in cols -%}
    {%- for v in cases[c] -%}
      {%- set r = [] -%}
      {%- for c2 in cols -%}
        {%- if c2 == c -%}{%- do r.append(data_parity_conformance_lit(c2, v, types) | trim ~ ' as ' ~ c2) -%}
        {%- else -%}{%- do r.append(data_parity_conformance_lit(c2, none, types) | trim ~ ' as ' ~ c2) -%}{%- endif -%}
      {%- endfor -%}
      {%- do rows.append(r) -%}
    {%- endfor -%}
  {%- endfor -%}
  {#- a few fully populated rows -#}
  {%- for k in range(4) -%}
    {%- set r = [] -%}
    {%- for c2 in cols -%}
      {%- set vals = cases[c2] -%}
      {%- do r.append(data_parity_conformance_lit(c2, vals[(k * 3 + loop.index0) % (vals | length)], types) | trim ~ ' as ' ~ c2) -%}
    {%- endfor -%}
    {%- do rows.append(r) -%}
  {%- endfor -%}

  {%- set selects = [] -%}
  {%- for r in rows -%}
    {%- do selects.append('select ' ~ loop.index0 ~ ' as id, ' ~ (r | join(', '))) -%}
  {%- endfor -%}

  {%- set hash_cols = [] -%}
  {%- for c in cols -%}{%- do hash_cols.append({'name': c, 'data_type': types[c]}) -%}{%- endfor -%}
  {%- set parts = data_parity_row_hash_parts(hash_cols) -%}
  {%- set canon_sel = [] -%}
  {%- for c in cols -%}
    {%- do canon_sel.append(data_parity_canonical(c, types[c]) | trim ~ ' as c_' ~ c) -%}
  {%- endfor -%}

  {%- set sql -%}
    select id, {{ canon_sel | join(', ') }}, {{ parts['h1'] }} as h1, {{ parts['h2'] }} as h2
    from ({{ selects | join('\nunion all\n') }}) t
    order by id
  {%- endset -%}

  {%- if execute -%}
    {%- set res = run_query(sql) -%}
    {%- for row in res.rows -%}
      {%- set out = ['PARITY|' ~ row[0]] -%}
      {%- for c in cols -%}
        {%- set v = row[loop.index] -%}
        {%- do out.append(c ~ '=' ~ (v if v is not none else '<SQLNULL>')) -%}
      {%- endfor -%}
      {%- do out.append('h1=' ~ row[9]) -%}
      {%- do out.append('h2=' ~ row[10]) -%}
      {{ log(out | join('|'), info=true) }}
    {%- endfor -%}
  {%- endif -%}
{% endmacro %}
