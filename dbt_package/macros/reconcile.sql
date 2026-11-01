{#
  data_parity_reconcile: Merkle-style drill-down of per-row hash aggregates. See docs/runbook.md and the data-parity CLI (src/data_parity/drill.py), which drives the three passes.

    dbt run-operation data_parity_reconcile [--args '{...}'] | sed -n 's/.*PARITY|//p' > side.txt
    (same on the other engine)  ->  diff side_a.txt side_b.txt

  Only aggregates are printed (counts and sums of row hashes). No row, no key, no business value.
  Partition values (e.g. dates) are printed; use a coarse partition column if those are sensitive.

  Args (all optional)
    only           [model, ...]     restrict to these models (a lone string is one model; an unknown name is an error)
    level          1|2|3            deepest pass allowed (default 3; a pass runs only if its arg is given)
    drill          {model: [partition, ...]}              pass 2: buckets of these partitions
    drill_buckets  {model: [[partition, bucket], ...]}    pass 3: per-column sums of these buckets
    buckets        256              number of buckets (same on both sides!)
    as_of          '2026-01-01'     rows whose partition value is >= as_of are excluded (partition column
                                    of type date/timestamp/int; rows with a NULL partition are excluded too)
    partitions     {last: N} | [p, ...]   scope pass 1 (and the table line) to the N latest non-NULL
                                    partitions (N integer >= 1) or to an explicit non-empty list; prints a
                                    scope| line. Any other shape, or a selection of 0 partitions while the
                                    table has some, is a compiler error
    rel_namespace  '[db.]schema'    read the model's table in this schema instead of ref() (symmetric, per side)
    relations      {model: '[db.]schema.table'}   table read for a model; wins over rel_namespace
    exclude_columns / exclude_prefixes / no_default_exclude   extra exclusions (`col` or `model.col`), see portable.sql

  Models: tagged `data_parity` or with config.meta.data_parity. meta.data_parity keys:
    partition_column (else config.event_time), primary_key (str or list; else inferred from a `unique`
    or dbt_utils.unique_combination_of_columns test), exclude_columns, exclude_prefixes [default meta_]
    (both: a string or a list of strings; an empty prefix is an error), compare_as (the key that matches the
    model with the one of the other side, default the model name; printed in the `## ` line), enabled.
    Opt out with `meta: {data_parity: false}` or `meta: {data_parity: {enabled: false}}`.

  Output (every line starts with PARITY|), per model, sorted:
    ## <model>
    cols|<name>:<type_class>,...        pk|<cols>|none        [as_of|<v>]  [scope|...]  [note|...]
    table|rows=|h1=|h2=                  (pass 1: one grouped scan; table = sum of the part lines)
    part|<p>|rows=|h1=|h2=               (pass 1)
    buckets|<N>                          (before the first bucket/col line)
    bucket|<p>|<b>|rows=|h1=|h2=         (pass 2)
    col|<p>|<b>|<column>|rows=|h1=|h2=   (pass 3)
  A model whose table does not exist on this side prints `## <key>` then `absent|<relation>` only.
  Without partition column, <p> is `*` (pass 1 prints the table line only). In pass 3 a bucket `*` means
  "the whole partition" (used for models without pk, see docs/runbook.md).
  NOTE| lines (not PARITY|) are informational: NOTE|<model>|excluded=<columns>.
#}

{# NULL partition sentinel, same as the canonical form (backslash + N). #}
{% macro data_parity_rc_null() %}{{ return('\\N') }}{% endmacro %}

{% macro data_parity_rc_lit(v) %}
  {%- set s = v | string -%}
  {%- if "'" in s or "\\" in s -%}
    {{ exceptions.raise_compiler_error("data_parity_reconcile: a partition value contains a quote or a backslash; refused") }}
  {%- endif -%}
  {{- return("'" ~ s ~ "'") -}}
{% endmacro %}

{# Partition info of a model. cls = type class of the partition column. For timestamps the partition
   is the UTC calendar day (cast to date). Returns none when there is no partition column. #}
{% macro data_parity_rc_pinfo(col, cls) %}
  {%- set q = adapter.quote(col['name']) -%}
  {%- if cls == 'date' -%}
    {{ return({'lhs': q, 'sqltype': 'date', 'canon_type': 'date', 'name': col['name']}) }}
  {%- elif cls == 'ts_tz' or cls == 'ts_naive' -%}
    {{ return({'lhs': 'cast(' ~ q ~ ' as date)', 'sqltype': 'date', 'canon_type': 'date', 'name': col['name']}) }}
  {%- elif cls == 'int' -%}
    {{ return({'lhs': q, 'sqltype': dbt.type_bigint(), 'canon_type': 'bigint', 'name': col['name']}) }}
  {%- elif cls == 'string' -%}
    {{ return({'lhs': q, 'sqltype': dbt.type_string(), 'canon_type': 'varchar', 'name': col['name']}) }}
  {%- else -%}
    {{ exceptions.raise_compiler_error("data_parity_reconcile: partition column " ~ col['name'] ~ " has type class '" ~ cls ~ "'; supported: date, timestamp, integer, string") }}
  {%- endif -%}
{% endmacro %}

{# SQL predicate "partition value is one of values" (strings; the NULL sentinel means IS NULL). #}
{% macro data_parity_rc_ppred(pinfo, values) %}
  {%- if pinfo is none -%}{{ return('(1 = 1)') }}{%- endif -%}
  {%- set items = [] -%}
  {%- set ns = namespace(has_null=false) -%}
  {%- for v in values -%}
    {%- set s = v | string -%}
    {%- if s == data_parity_rc_null() | trim -%}
      {%- set ns.has_null = true -%}
    {%- else -%}
      {%- do items.append('cast(' ~ (data_parity_rc_lit(s) | trim) ~ ' as ' ~ pinfo['sqltype'] ~ ')') -%}
    {%- endif -%}
  {%- endfor -%}
  {%- set conds = [] -%}
  {%- if items | length > 0 -%}{%- do conds.append(pinfo['lhs'] ~ ' in (' ~ items | join(', ') ~ ')') -%}{%- endif -%}
  {%- if ns.has_null -%}{%- do conds.append(pinfo['lhs'] ~ ' is null') -%}{%- endif -%}
  {%- if conds | length == 0 -%}{{ return('(1 = 0)') }}{%- endif -%}
  {{- return('(' ~ conds | join(' or ') ~ ')') -}}
{% endmacro %}

{# Canonical text of the partition value, as a SQL expression. #}
{% macro data_parity_rc_ptext(pinfo) %}
  {%- if pinfo is none -%}{{ return("'*'") }}{%- endif -%}
  {{- return(data_parity_canonical(pinfo['lhs'], pinfo['canon_type']) | trim) -}}
{% endmacro %}

{# Generic aggregate query.
     ptext   : SQL text expression of the partition (alias dp_p) or none
     bexpr   : SQL bucket expression (alias b) or none
     exprs   : list of {alias, expr} (integer SQL expressions, summed)
   Output row: [dp_p] [b] count, sum(expr1), sum(expr2), ... (all cast to string), ordered by dp_p, b. #}
{% macro data_parity_rc_agg_sql(rel, ptext, bexpr, exprs, where) %}
  {%- set s = data_parity_string_type() | trim -%}
  {%- set inner = [] -%}
  {%- set outer = [] -%}
  {%- set groups = [] -%}
  {%- if ptext is not none -%}
    {%- do inner.append(ptext ~ ' as dp_p') -%}{%- do outer.append('dp_p') -%}{%- do groups.append('dp_p') -%}
  {%- endif -%}
  {%- if bexpr is not none -%}
    {%- do inner.append(bexpr ~ ' as b') -%}{%- do outer.append('cast(b as ' ~ s ~ ') as b_txt') -%}{%- do groups.append('b') -%}
  {%- endif -%}
  {%- do outer.append('cast(count(*) as ' ~ s ~ ') as n') -%}
  {%- for e in exprs -%}
    {%- do inner.append(e['expr'] ~ ' as ' ~ e['alias']) -%}
    {%- do outer.append('cast(coalesce(sum(' ~ e['alias'] ~ '), 0) as ' ~ s ~ ') as s_' ~ e['alias']) -%}
  {%- endfor -%}
  {{- return('select ' ~ outer | join(', ') ~ ' from (select ' ~ inner | join(', ') ~ ' from ' ~ rel ~ (' where ' ~ where if where else '') ~ ') t' ~ (' group by ' ~ groups | join(', ') ~ ' order by ' ~ groups | join(', ') if groups | length > 0 else '')) -}}
{% endmacro %}

{% macro data_parity_rc_and(preds) %}
  {%- set ps = [] -%}
  {%- for p in preds if p -%}{%- do ps.append(p) -%}{%- endfor -%}
  {{- return(ps | join(' and ')) -}}
{% endmacro %}

{# models carrying the tag or the meta #}
{% macro data_parity_rc_models(only) %}
  {%- set only = data_parity_cfg_list(only, 'only', none) -%}
  {%- set models = [] -%}
  {%- set tagged = [] -%}
  {%- set owner = {} -%}
  {%- for node in graph.nodes.values() if node.resource_type == 'model' -%}
    {%- set meta = (node.config.meta or {}).get('data_parity') -%}
    {%- if (meta is not none or 'data_parity' in node.tags) and not data_parity_meta_disabled(meta) -%}
      {%- set key = data_parity_rc_key(node, meta) -%}
      {%- if key in owner -%}
        {{ exceptions.raise_compiler_error("data_parity: models '" ~ owner[key] ~ "' and '" ~ node.name ~ "' have the same match key '" ~ key ~ "' (meta.data_parity.compare_as) on this side") }}
      {%- endif -%}
      {%- do owner.update({key: node.name}) -%}
      {%- do tagged.append(node.name) -%}
      {%- if key != node.name -%}{%- do tagged.append(key) -%}{%- endif -%}
      {%- if only is none or node.name in only or key in only -%}
        {%- do models.append(node) -%}
      {%- endif -%}
    {%- endif -%}
  {%- endfor -%}
  {%- if only is not none -%}
    {%- set unknown = [] -%}
    {%- for n in only if n not in tagged -%}{%- do unknown.append(n) -%}{%- endfor -%}
    {%- if unknown | length > 0 -%}
      {{ exceptions.raise_compiler_error("data_parity: 'only' names no tagged model: " ~ unknown | join(', ') ~ " (tagged: " ~ tagged | sort | join(', ') ~ ")") }}
    {%- endif -%}
  {%- endif -%}
  {{- return(models | sort(attribute='name')) -}}
{% endmacro %}

{# primary key columns: meta.primary_key, else unique / unique_combination_of_columns tests. [] if none. #}
{% macro data_parity_rc_pk(node, meta) %}
  {%- set declared = meta.get('primary_key') -%}
  {%- if declared -%}
    {%- set lst = [declared] if declared is string else declared -%}
    {%- set normed = [] -%}
    {%- for k in lst -%}{%- do normed.append(data_parity_norm_name(k) | trim) -%}{%- endfor -%}
    {{- return(normed) -}}
  {%- endif -%}
  {%- set singles = [] -%}
  {%- set combos = [] -%}
  {%- for t in graph.nodes.values() if t.resource_type == 'test' -%}
    {%- set tm = t.get('test_metadata') -%}
    {%- if tm and (t.get('attached_node') == node.unique_id or node.unique_id in (t.get('depends_on', {}).get('nodes', []) or [])) -%}
      {%- set kw = tm.get('kwargs') or {} -%}
      {%- if tm.get('name') == 'unique' -%}
        {%- set cn = kw.get('column_name') or t.get('column_name') -%}
        {%- if cn -%}{%- do singles.append(data_parity_norm_name(cn) | trim) -%}{%- endif -%}
      {%- elif tm.get('name') == 'unique_combination_of_columns' -%}
        {%- set cc = kw.get('combination_of_columns') -%}
        {%- if cc -%}
          {%- set normed_cc = [] -%}
          {%- for k in cc -%}{%- do normed_cc.append(data_parity_norm_name(k) | trim) -%}{%- endfor -%}
          {%- do combos.append(normed_cc | sort | list) -%}
        {%- endif -%}
      {%- endif -%}
    {%- endif -%}
  {%- endfor -%}
  {%- if singles | length > 0 -%}{{ return([singles | sort | first]) }}{%- endif -%}
  {%- if combos | length > 0 -%}{{ return(combos | first) }}{%- endif -%}
  {{- return([]) -}}
{% endmacro %}

{% macro data_parity_reconcile(only=none, level=3, drill=none, drill_buckets=none, buckets=256, as_of=none, partitions=none, rel_namespace=none, relations=none, exclude_columns=none, exclude_prefixes=none, no_default_exclude=false) %}
  {% if not execute %}{{ return('') }}{% endif %}
  {% set level = level | int %}
  {% set nb = buckets | int %}
  {% if nb < 1 %}{% do exceptions.raise_compiler_error('data_parity_reconcile: buckets must be >= 1') %}{% endif %}
  {% set drill = drill if drill is mapping else {} %}
  {% set drill_buckets = drill_buckets if drill_buckets is mapping else {} %}
  {% set nul = data_parity_rc_null() | trim %}
  {% set S = data_parity_string_type() | trim %}
  {% set out = [] %}
  {% if partitions is not none %}
    {% if partitions is mapping %}
      {% if partitions.keys() | list != ['last'] or partitions.get('last') is boolean or partitions.get('last') is not integer or partitions.get('last') < 1 %}
        {% do exceptions.raise_compiler_error("data_parity_reconcile: partitions must be {last: N} with N an integer >= 1, or a non-empty list of partition values; got " ~ (partitions | string)) %}
      {% endif %}
    {% elif partitions is string or partitions is not sequence or partitions | length == 0 %}
      {% do exceptions.raise_compiler_error("data_parity_reconcile: partitions must be {last: N} with N an integer >= 1, or a non-empty list of partition values; got " ~ (partitions | string)) %}
    {% endif %}
  {% endif %}
  {% set models = data_parity_rc_models(only) %}
  {% if models | length == 0 %}
    {% do exceptions.raise_compiler_error('data_parity_reconcile: no model tagged data_parity') %}
  {% endif %}

  {% for node in models %}
    {% set meta = (node.config.meta or {}).get('data_parity') %}
    {% set meta = meta if meta is mapping else {} %}
    {% set rel = data_parity_rc_relation(node, ref(node.name), rel_namespace, relations) %}
    {% set excl = data_parity_rc_exclusions(node, meta, exclude_columns, exclude_prefixes, data_parity_cfg_bool(no_default_exclude, 'no_default_exclude')) %}
    {% set excl_c = excl['cols'] %}
    {% set excl_p = excl['prefixes'] %}
    {% set excluded = [] %}
    {% set pcol_name = data_parity_norm_name(meta.get('partition_column') or meta.get('date_column') or node.config.get('event_time') or '') | trim %}

    {# ---- columns: matched on the normalized name (a/b and a_b are the same column), minus exclusions #}
    {% set cols = [] %}
    {% set seen = {} %}
    {% set live_cols = adapter.get_columns_in_relation(rel) %}
    {% set key = data_parity_rc_key(node, meta) %}
    {# no such table on this side: reported as `absent|<relation>`; the CLI turns it into "only on the other side"
       (or an error when no model of this side has a table: wrong namespace / mapping) #}
    {% if live_cols | length == 0 %}
      {% do out.append('PARITY|## ' ~ key) %}
      {% do out.append('PARITY|absent|' ~ rel) %}
    {% else %}
    {% for c in live_cols %}
      {% set n = data_parity_norm_name(c.name) | trim %}
      {% if n in seen %}
        {% do exceptions.raise_compiler_error('data_parity_reconcile: ' ~ node.name ~ ": columns '" ~ seen[n] ~ "' and '" ~ c.name ~ "' are the same column once normalized (" ~ n ~ ')') %}
      {% endif %}
      {% do seen.update({n: c.name}) %}
      {% set skip = namespace(value = n in excl_c) %}
      {% for p in excl_p %}{% if n.startswith(p) %}{% set skip.value = true %}{% endif %}{% endfor %}
      {% if not skip.value %}
        {% do cols.append({'name': c.name, 'lname': n, 'data_type': c.data_type, 'cls': data_parity_type_class(c.data_type, target.type == 'bigquery') | trim}) %}
      {% else %}
        {% do excluded.append(n) %}
      {% endif %}
    {% endfor %}
    {% set cols = cols | sort(attribute='lname') %}
    {% set cls_of = {} %}
    {% for c in cols %}{% do cls_of.update({c['lname']: c}) %}{% endfor %}

    {# ---- partition, primary key #}
    {% set pinfo = none %}
    {% if pcol_name %}
      {% if pcol_name not in cls_of %}
        {% do exceptions.raise_compiler_error('data_parity_reconcile: ' ~ node.name ~ ': partition column ' ~ pcol_name ~ ' not found (or excluded)') %}
      {% endif %}
      {% set pinfo = data_parity_rc_pinfo(cls_of[pcol_name], cls_of[pcol_name]['cls']) %}
    {% endif %}
    {% set pk = data_parity_rc_pk(node, meta) %}
    {% set pk_cols = [] %}
    {% for k in pk %}
      {% if k not in cls_of %}
        {% do exceptions.raise_compiler_error('data_parity_reconcile: ' ~ node.name ~ ': primary key column ' ~ k ~ ' not found (or excluded)') %}
      {% endif %}
      {% do pk_cols.append(cls_of[k]) %}
    {% endfor %}

    {# ---- hash expressions #}
    {% set full = data_parity_row_hash_parts(cols) %}
    {% set kh = data_parity_row_hash_parts(pk_cols if pk_cols | length > 0 else cols) %}
    {% set bexpr = 'mod(' ~ kh['h1'] ~ ', ' ~ nb ~ ')' %}
    {% set ptext = data_parity_rc_ptext(pinfo) | trim %}
    {% set base_exprs = [{'alias': 'h1', 'expr': full['h1']}, {'alias': 'h2', 'expr': full['h2']}] %}

    {# ---- as_of filter #}
    {% set asof_pred = '' %}
    {% if as_of is not none and pinfo is not none %}
      {% if pinfo['sqltype'] == dbt.type_string() %}
        {% do exceptions.raise_compiler_error('data_parity_reconcile: as_of needs a date, timestamp or integer partition column') %}
      {% endif %}
      {% set asof_pred = '(' ~ pinfo['lhs'] ~ ' < cast(' ~ (data_parity_rc_lit(as_of) | trim) ~ ' as ' ~ pinfo['sqltype'] ~ '))' %}
    {% endif %}

    {% do out.append('PARITY|## ' ~ key) %}
    {% do out.append('NOTE|' ~ key ~ '|excluded=' ~ (excluded | sort | join(',') if excluded | length > 0 else 'none')) %}
    {% set colparts = [] %}
    {% for c in cols %}{% do colparts.append(c['lname'] ~ ':' ~ c['cls']) %}{% endfor %}
    {% do out.append('PARITY|cols|' ~ colparts | join(',')) %}
    {% do out.append('PARITY|pk|' ~ (pk | join(',') if pk | length > 0 else 'none')) %}
    {% if as_of is not none %}
      {% if pinfo is none %}
        {% do out.append('PARITY|note|as_of ignored: no partition column') %}
      {% else %}
        {% do out.append('PARITY|as_of|' ~ as_of) %}
      {% endif %}
    {% endif %}

    {# ---- scope (pass 1) #}
    {% set scope_pred = '' %}
    {% if partitions is not none and pinfo is none %}
      {% do out.append('PARITY|note|partitions scope ignored: no partition column') %}
    {% elif partitions is not none %}
      {% set all_sql = 'select distinct ' ~ ptext ~ ' as dp_p from ' ~ rel ~ (' where ' ~ asof_pred if asof_pred else '') ~ ' order by 1' %}
      {% set all_parts = [] %}
      {% for r in run_query(all_sql).rows %}{% set v = r[0] | string %}{% if v != nul %}{% do all_parts.append(v) %}{% endif %}{% endfor %}
      {% set chosen = [] %}
      {% if partitions is mapping %}
        {% set n_last = partitions.get('last') %}
        {% set start = all_parts | length - n_last %}
        {% for v in all_parts %}{% if loop.index0 >= start %}{% do chosen.append(v) %}{% endif %}{% endfor %}
        {% do out.append('PARITY|scope|last=' ~ n_last ~ '|checked=' ~ chosen | length ~ '|total=' ~ all_parts | length ~ '|from=' ~ (chosen | first if chosen | length > 0 else '') ~ '|to=' ~ (chosen | last if chosen | length > 0 else '')) %}
      {% else %}
        {% for v in partitions %}{% if (v | string) in all_parts %}{% do chosen.append(v | string) %}{% endif %}{% endfor %}
        {% do out.append('PARITY|scope|list|checked=' ~ chosen | length ~ '|total=' ~ all_parts | length) %}
      {% endif %}
      {% if chosen | length == 0 and all_parts | length > 0 %}
        {% do exceptions.raise_compiler_error('data_parity_reconcile: ' ~ node.name ~ ': partitions selected 0 of ' ~ all_parts | length ~ ' partitions (check the partition values)') %}
      {% endif %}
      {% set scope_pred = data_parity_rc_ppred(pinfo, chosen) | trim %}
    {% endif %}

    {# ---- pass 1: ONE grouped scan; table line = sum of the parts #}
    {% if pinfo is none %}
      {% do out.append('PARITY|note|no partition column: table line only, buckets are taken table-wide (partition value *)') %}
      {% set sql1 = data_parity_rc_agg_sql(rel, none, none, base_exprs, data_parity_rc_and([asof_pred])) %}
      {% set rows1 = run_query(sql1).rows %}
      {% set r = rows1[0] | list %}
      {% do out.append('PARITY|table|rows=' ~ r[0] ~ '|h1=' ~ r[1] ~ '|h2=' ~ r[2]) %}
    {% else %}
      {% set sql1 = data_parity_rc_agg_sql(rel, ptext, none, base_exprs, data_parity_rc_and([asof_pred, scope_pred])) %}
      {% set rows1 = run_query(sql1).rows %}
      {% set t = namespace(n=0, a=0, b=0) %}
      {% for r in rows1 %}
        {% set r = r | list %}
        {% set t.n = t.n + (r[1] | int) %}{% set t.a = t.a + (r[2] | int) %}{% set t.b = t.b + (r[3] | int) %}
      {% endfor %}
      {% do out.append('PARITY|table|rows=' ~ t.n ~ '|h1=' ~ t.a ~ '|h2=' ~ t.b) %}
      {% for r in rows1 %}
        {% set r = r | list %}
        {% do out.append('PARITY|part|' ~ r[0] ~ '|rows=' ~ r[1] ~ '|h1=' ~ r[2] ~ '|h2=' ~ r[3]) %}
      {% endfor %}
    {% endif %}

    {# ---- pass 2: buckets of the drilled partitions #}
    {% set d_parts = drill.get(node.name) %}
    {% set run2 = level >= 2 and d_parts is not none and d_parts | length > 0 %}
    {% set pairs = drill_buckets.get(node.name) %}
    {% set run3 = level >= 3 and pairs is not none and pairs | length > 0 %}
    {% if run2 or run3 %}{% do out.append('PARITY|buckets|' ~ nb) %}{% endif %}
    {% if run2 %}
      {% set where2 = data_parity_rc_and([asof_pred, data_parity_rc_ppred(pinfo, d_parts) | trim if pinfo is not none else '']) %}
      {% set sql2 = data_parity_rc_agg_sql(rel, ptext, bexpr, base_exprs, where2) %}
      {% for r in run_query(sql2).rows %}
        {% set r = r | list %}
        {% do out.append('PARITY|bucket|' ~ r[0] ~ '|' ~ r[1] ~ '|rows=' ~ r[2] ~ '|h1=' ~ r[3] ~ '|h2=' ~ r[4]) %}
      {% endfor %}
    {% endif %}

    {# ---- pass 3: per column, hash over [pk columns + column], restricted to (partition, bucket) #}
    {% if run3 %}
      {% set pk_names = pk %}
      {% set cexprs = [] %}
      {% set cnames = [] %}
      {% for c in cols if c['lname'] not in pk_names %}
        {% set hp = data_parity_row_hash_parts(pk_cols + [c]) %}
        {% do cexprs.append({'alias': 'c' ~ loop.index0 ~ 'h1', 'expr': hp['h1']}) %}
        {% do cexprs.append({'alias': 'c' ~ loop.index0 ~ 'h2', 'expr': hp['h2']}) %}
        {% do cnames.append(c['lname']) %}
      {% endfor %}
      {# pairs grouped by partition: specific buckets, and whole partitions (bucket *) #}
      {% set by_p = {} %}
      {% set whole = [] %}
      {% for pr in pairs %}
        {% set pv = pr[0] | string %}{% set bv = pr[1] | string %}
        {% if bv == '*' %}
          {% if pv not in whole %}{% do whole.append(pv) %}{% endif %}
        {% else %}
          {% if pv not in by_p %}{% do by_p.update({pv: []}) %}{% endif %}
          {% do by_p[pv].append(bv | int | string) %}
        {% endif %}
      {% endfor %}
      {% set specs = [] %}
      {% if by_p | length > 0 %}
        {% set ors = [] %}
        {% for pv in by_p.keys() | sort %}
          {% do ors.append('(' ~ data_parity_rc_ppred(pinfo, [pv]) | trim ~ ' and ' ~ bexpr ~ ' in (' ~ by_p[pv] | join(', ') ~ '))') %}
        {% endfor %}
        {% do specs.append({'bexpr': bexpr, 'where': '(' ~ ors | join(' or ') ~ ')'}) %}
      {% endif %}
      {% if whole | length > 0 %}
        {% do specs.append({'bexpr': "'*'", 'where': data_parity_rc_ppred(pinfo, whole) | trim}) %}
      {% endif %}
      {% set col_lines = [] %}
      {% for sp in specs %}
        {% set sql3 = data_parity_rc_agg_sql(rel, ptext, sp['bexpr'], cexprs, data_parity_rc_and([asof_pred, sp['where']])) %}
        {% for r in run_query(sql3).rows %}
          {% set r = r | list %}
          {% for cn in cnames %}
            {% set i = loop.index0 %}
            {% do col_lines.append('PARITY|col|' ~ r[0] ~ '|' ~ r[1] ~ '|' ~ cn ~ '|rows=' ~ r[2] ~ '|h1=' ~ r[3 + 2 * i] ~ '|h2=' ~ r[4 + 2 * i]) %}
          {% endfor %}
        {% endfor %}
      {% endfor %}
      {% for l in col_lines %}{% do out.append(l) %}{% endfor %}
    {% endif %}
    {% endif %}
  {% endfor %}

  {{ log(out | join('\n'), info=true) }}
{% endmacro %}
