{#
  Closed-partition cache for data-parity pass 1. See docs/cache.md.

    dbt run-operation data_parity_cache_refresh  --args '{schema: dp_cache}'                 # dry run, creates nothing
    dbt run-operation data_parity_cache_refresh  --args '{schema: dp_cache, write: true}'    # fills the cache
    dbt run-operation data_parity_cached_pass1   --args '{schema: dp_cache}' | sed -n 's/.*PARITY|//p'
    dbt run-operation data_parity_cache_verify   --args '{schema: dp_cache, sample: 5}'

  Only counts and sums of hashes are stored / printed. `PARITY|` lines are the pass-1
  contract and are byte-identical to a full live scan; everything informative uses `NOTE|`
  (or `VERIFY|`) so it never reaches a diff.
#}

{# ---------------------------------------------------------------- helpers #}

{% macro data_parity_cache_resolve_schema(schema, required) %}
  {% set s = schema or var('data_parity_cache_schema', none) %}
  {% if s is not none and not modules.re.match('^[A-Za-z0-9_-]+$', s | string) %}
    {% do exceptions.raise_compiler_error("data_parity cache: invalid schema '" ~ s ~ "' (letters, digits, underscore and hyphen only)") %}
  {% endif %}
  {% if s is none and required %}
    {% do exceptions.raise_compiler_error("data_parity cache: no cache schema. Pass schema: <name> or set var data_parity_cache_schema (there is deliberately no default).") %}
  {% endif %}
  {{ return(s) }}
{% endmacro %}

{% macro data_parity_cache_version() %}
  {{ return(var('data_parity_cache_version', '1') | string) }}
{% endmacro %}

{# md5 hex of a string, computed by dbt (not by the warehouse). #}
{% macro data_parity_cache_md5(s) %}
  {{ return(local_md5(s)) }}
{% endmacro %}

{# Closed boundary: partitions strictly before this date are closed. Returns 'YYYY-MM-DD'. #}
{% macro data_parity_cache_cutoff(reference_date, lag_days) %}
  {% if not modules.re.match('^[0-9]+$', lag_days | string) %}
    {% do exceptions.raise_compiler_error("data_parity cache: lag_days must be an integer >= 0, got " ~ (lag_days | string)) %}
  {% endif %}
  {% set base = ("cast('" ~ (reference_date | string | replace("'", "")) ~ "' as date)") if reference_date else 'current_date' %}
  {% set sql = 'select cast(' ~ data_parity_cache_date_sub(base, lag_days | int) ~ ' as ' ~ dbt.type_string() ~ ')' %}
  {{ return(run_query(sql).rows[0][0] | string) }}
{% endmacro %}

{# Models to process, with everything derived from the live schema. #}
{% macro data_parity_cache_models(only) %}
  {% set only = data_parity_cfg_list(only, 'only', none) %}
  {% set out = [] %}
  {% set tagged = [] %}
  {% for node in graph.nodes.values() if node.resource_type == 'model' %}
    {% set meta = (node.config.meta or {}).get('data_parity') %}
    {% if (meta is not none or 'data_parity' in node.tags) and not data_parity_meta_disabled(meta) %}
      {% do tagged.append(node.name) %}
    {% endif %}
    {% if (meta is not none or 'data_parity' in node.tags) and not data_parity_meta_disabled(meta)
          and (only is none or node.name in only) %}
      {% set meta = meta if meta is mapping else {} %}
      {% set rel = ref(node.name) %}
      {% set excl = data_parity_rc_exclusions(node, meta, none, none, false) %}
      {% set excl_c = excl['cols'] %}
      {% set excl_p = excl['prefixes'] %}
      {% set excluded = [] %}
      {% set part_col = data_parity_norm_name(meta.get('partition_column') or node.config.get('event_time') or '') | trim %}
      {% set bq = (target.type == 'bigquery') %}
      {% set by_name = {} %}
      {% for c in adapter.get_columns_in_relation(rel) %}
        {% set n = data_parity_norm_name(c.name) | trim %}
        {% if n in by_name %}
          {% do exceptions.raise_compiler_error("data_parity cache: " ~ node.name ~ ": columns '" ~ by_name[n]['name'] ~ "' and '" ~ c.name ~ "' are the same column once normalized (" ~ n ~ ")") %}
        {% endif %}
        {% set skip = namespace(value = n in excl_c) %}
        {% for p in excl_p %}{% if n.startswith(p) %}{% set skip.value = true %}{% endif %}{% endfor %}
        {% if not skip.value %}
          {% do by_name.update({n: {'name': c.name, 'data_type': c.data_type, 'cls': data_parity_type_class(c.data_type, bq) | trim}}) %}
        {% else %}
          {% do excluded.append(n) %}
        {% endif %}
      {% endfor %}
      {% set cols = [] %}
      {% set pieces = [] %}
      {% for n in by_name.keys() | list | sort %}
        {% do cols.append(by_name[n]) %}
        {% do pieces.append(n ~ ':' ~ by_name[n]['cls']) %}
      {% endfor %}
      {% set cols_line = pieces | join(',') %}
      {% set pexpr = none %}
      {% if part_col %}
        {% set pc = by_name.get(part_col) %}
        {% if pc is none %}
          {% do exceptions.raise_compiler_error("data_parity cache: partition column '" ~ part_col ~ "' not found (or excluded) in " ~ node.name) %}
        {% endif %}
        {% set q = adapter.quote(pc['name']) %}
        {% set pexpr = q if pc['cls'] == 'date' else 'cast(' ~ q ~ ' as date)' %}
      {% endif %}
      {# schema fingerprint: columns + types classes + canonical form version #}
      {% set fp = data_parity_cache_md5('spec1;' ~ cols_line) %}
      {% do out.append({'name': node.name, 'rel': rel, 'cols': cols, 'cols_line': cols_line, 'pexpr': pexpr, 'schema_fp': fp, 'node': node, 'meta': meta, 'excluded': excluded | sort | join(',')}) %}
    {% endif %}
  {% endfor %}
  {% if only is not none %}
    {% set unknown = [] %}
    {% for n in only if n not in tagged %}{% do unknown.append(n) %}{% endfor %}
    {% if unknown | length > 0 %}
      {% do exceptions.raise_compiler_error("data_parity: 'only' names no tagged model: " ~ unknown | join(', ') ~ " (tagged: " ~ tagged | sort | join(', ') ~ ")") %}
    {% endif %}
  {% endif %}
  {{ return(out | sort(attribute='name')) }}
{% endmacro %}

{# Live aggregate: list of [partition, rows, h1, h2] (strings), grouped by partition (or one row, partition 'None'). #}
{% macro data_parity_cache_live(m, where_sql) %}
  {% set parts = data_parity_row_hash_parts(m['cols']) %}
  {% set big = dbt.type_bigint() %}
  {% set pstr = ('cast(' ~ m['pexpr'] ~ ' as ' ~ dbt.type_string() ~ ')') if m['pexpr'] else 'cast(null as ' ~ dbt.type_string() ~ ')' %}
  {% set sql = 'select ' ~ pstr ~ ' as p, count(*) as n, cast(sum(' ~ parts['h1'] ~ ') as ' ~ big ~ ') as s1, cast(sum(' ~ parts['h2'] ~ ') as ' ~ big ~ ') as s2'
      ~ ' from ' ~ m['rel'] ~ (' where ' ~ where_sql if where_sql else '') ~ (' group by 1 order by 1' if m['pexpr'] else '') %}
  {% set res = [] %}
  {% for r in run_query(sql).rows %}
    {% do res.append([(r[0] | string) if r[0] is not none else (data_parity_rc_null() | trim), r[1] | int, r[2] | int if r[2] is not none else 0, r[3] | int if r[3] is not none else 0]) %}
  {% endfor %}
  {{ return({'rows': res, 'sql': sql}) }}
{% endmacro %}

{% macro data_parity_cache_date_list(parts) %}
  {%- set l = [] -%}{%- for p in parts -%}{%- do l.append("date '" ~ p ~ "'") -%}{%- endfor -%}
  {{- return(l | join(', ')) -}}
{% endmacro %}

{# Short form of a list of partitions for NOTE lines. #}
{% macro data_parity_cache_brief(l) %}
  {% if l | length <= 8 %}{{ return(l | join(',')) }}{% endif %}
  {{ return(l[:4] | join(',') ~ ',...(' ~ (l | length - 7) ~ ' more),' ~ l[-3:] | join(',')) }}
{% endmacro %}

{# Cache rows of one model: list of dicts. Empty if the table does not exist. #}
{% macro data_parity_cache_read(model_name, schema) %}
  {% if schema is none %}{{ return([]) }}{% endif %}
  {% set tbl = adapter.get_relation(database=target.database, schema=schema, identifier='data_parity_cache') %}
  {% if tbl is none %}{{ return([]) }}{% endif %}
  {% set res = [] %}
  {% set sql = "select partition_value, rows_n, h1_sum, h2_sum, schema_fp, version from " ~ data_parity_cache_fqn(schema) ~ " where model = '" ~ model_name ~ "' order by computed_at" %}
  {# oldest first: for a partition with several rows the newest wins (cached.update in the caller) #}
  {% for r in run_query(sql).rows %}
    {% do res.append({'p': r[0] | string, 'n': r[1] | int, 'h1': r[2] | int, 'h2': r[3] | int, 'fp': r[4] | string, 'ver': r[5] | string}) %}
  {% endfor %}
  {{ return(res) }}
{% endmacro %}

{# What is closed, trusted, stale, missing. Reads only the partition column live. #}
{% macro data_parity_cache_plan(m, cutoff, schema) %}
  {% set version = data_parity_cache_version() %}
  {% set closed = [] %}
  {% set sql = 'select distinct cast(' ~ m['pexpr'] ~ ' as ' ~ dbt.type_string() ~ ') as p from ' ~ m['rel'] ~ ' where ' ~ m['pexpr'] ~ " < date '" ~ cutoff ~ "' order by 1" %}
  {% for r in run_query(sql).rows %}{% do closed.append(r[0] | string) %}{% endfor %}
  {% set cached = {} %}
  {% for r in data_parity_cache_read(m['name'], schema) %}{% do cached.update({r['p']: r}) %}{% endfor %}
  {% set valid = {} %}
  {% set stale = [] %}
  {% set missing = [] %}
  {% for p in closed %}
    {% set c = cached.get(p) %}
    {% if c is none %}{% do missing.append(p) %}
    {% elif c['fp'] == m['schema_fp'] and c['ver'] == version %}{% do valid.update({p: c}) %}
    {% else %}{% do stale.append(p) %}{% endif %}
  {% endfor %}
  {{ return({'closed': closed, 'valid': valid, 'stale': stale, 'missing': missing, 'todo': stale + missing, 'version': version}) }}
{% endmacro %}

{# Scan the given closed partitions live (all closed ones: range predicate, else IN list). #}
{% macro data_parity_cache_scan_closed(m, plan, cutoff) %}
  {% if plan['todo'] | length == 0 %}{{ return({'rows': [], 'sql': ''}) }}{% endif %}
  {% if plan['todo'] | length == plan['closed'] | length %}
    {% set w = m['pexpr'] ~ " < date '" ~ cutoff ~ "'" %}
  {% else %}
    {% set w = m['pexpr'] ~ ' in (' ~ data_parity_cache_date_list(plan['todo']) ~ ')' %}
  {% endif %}
  {{ return(data_parity_cache_live(m, w)) }}
{% endmacro %}

{# ---------------------------------------------------------------- 1. refresh #}

{% macro data_parity_cache_refresh(only=none, lag_days=3, reference_date=none, schema=none, write=false) %}
  {% if not execute %}{{ return('') }}{% endif %}
  {% set write = data_parity_cfg_bool(write, 'write') %}
  {% set schema = data_parity_cache_resolve_schema(schema, write) %}
  {% set cutoff = data_parity_cache_cutoff(reference_date, lag_days) %}
  {% set out = [] %}
  {% do out.append('NOTE|refresh|mode=' ~ ('WRITE' if write else 'DRY RUN (write=false, nothing is created or modified)') ~ '|cutoff=' ~ cutoff ~ '|lag_days=' ~ lag_days ~ '|schema=' ~ schema ~ '|version=' ~ data_parity_cache_version()) %}
  {% for m in data_parity_cache_models(only) %}
    {% if not m['pexpr'] %}
      {% do out.append('NOTE|refresh|' ~ m['name'] ~ '|no partition column: nothing to cache') %}
    {% else %}
      {% set plan = data_parity_cache_plan(m, cutoff, schema) %}
      {% do out.append('NOTE|refresh|' ~ m['name'] ~ '|closed=' ~ plan['closed'] | length ~ '|cached_valid=' ~ plan['valid'] | length ~ '|stale=' ~ plan['stale'] | length ~ '|missing=' ~ plan['missing'] | length) %}
      {% if plan['todo'] | length == 0 %}
        {% do out.append('NOTE|refresh|' ~ m['name'] ~ '|up to date, nothing to do') %}
      {% elif not write %}
        {% if plan['missing'] %}{% do out.append('NOTE|refresh|' ~ m['name'] ~ '|WOULD compute+insert missing: ' ~ data_parity_cache_brief(plan['missing'])) %}{% endif %}
        {% if plan['stale'] %}{% do out.append('NOTE|refresh|' ~ m['name'] ~ '|WOULD recompute+replace stale (schema_fp or version differs): ' ~ data_parity_cache_brief(plan['stale'])) %}{% endif %}
      {% else %}
        {% do data_parity_cache_create_schema(schema) %}
        {% do data_parity_cache_create_table(schema) %}
        {% set scan = data_parity_cache_scan_closed(m, plan, cutoff) %}
        {% set fqn = data_parity_cache_fqn(schema) %}
        {# Append-only by default (newest row wins on read): DELETE is not supported by every connector
           (e.g. Trino `memory`). Opt in with var data_parity_cache_prune=true to remove superseded rows. #}
        {% if plan['stale'] and var('data_parity_cache_prune', false) %}
          {% set q = [] %}{% for p in plan['stale'] %}{% do q.append("'" ~ p ~ "'") %}{% endfor %}
          {% do run_query("delete from " ~ fqn ~ " where model = '" ~ m['name'] ~ "' and partition_value in (" ~ q | join(', ') ~ ")") %}
        {% endif %}
        {% set vals = [] %}
        {% for r in scan['rows'] %}
          {% do vals.append("('" ~ m['name'] ~ "', '" ~ r[0] ~ "', " ~ r[1] ~ ', ' ~ r[2] ~ ', ' ~ r[3] ~ ", '" ~ m['schema_fp'] ~ "', '" ~ plan['version'] ~ "', current_timestamp)") %}
        {% endfor %}
        {% for i in range(0, vals | length, 500) %}
          {% do run_query('insert into ' ~ fqn ~ ' (model, partition_value, rows_n, h1_sum, h2_sum, schema_fp, version, computed_at) values ' ~ vals[i:i + 500] | join(', ')) %}
        {% endfor %}
        {% do data_parity_cache_commit() %}
        {% do out.append('NOTE|refresh|' ~ m['name'] ~ '|inserted=' ~ vals | length ~ ' (superseding stale=' ~ plan['stale'] | length ~ ', prune=' ~ (var('data_parity_cache_prune', false) | string | lower) ~ ')') %}
      {% endif %}
    {% endif %}
  {% endfor %}
  {{ log(out | join('\n'), info=true) }}
{% endmacro %}

{# ---------------------------------------------------------------- 2. cached pass 1 #}

{% macro data_parity_cached_pass1(only=none, lag_days=3, reference_date=none, schema=none, as_of=none, partitions=none) %}
  {% if not execute %}{{ return('') }}{% endif %}
  {% if as_of is not none or partitions is not none %}
    {% do exceptions.raise_compiler_error("data_parity_cached_pass1 does not support as_of / partitions (they only exist in data_parity_reconcile); the cache serves whole closed partitions") %}
  {% endif %}
  {% set schema = data_parity_cache_resolve_schema(schema, true) %}
  {% set cutoff = data_parity_cache_cutoff(reference_date, lag_days) %}
  {% set out = [] %}
  {% for m in data_parity_cache_models(only) %}
    {% set notes = [] %}
    {% set parts = {} %}
    {% if m['pexpr'] %}
      {% set plan = data_parity_cache_plan(m, cutoff, schema) %}
      {% for p, c in plan['valid'].items() %}{% do parts.update({p: [c['n'], c['h1'], c['h2']]}) %}{% endfor %}
      {# live: partitions not trusted in cache (missing / stale) + open ones (>= cutoff) + null partition #}
      {% set scan = data_parity_cache_scan_closed(m, plan, cutoff) %}
      {% for r in scan['rows'] %}{% do parts.update({r[0]: [r[1], r[2], r[3]]}) %}{% endfor %}
      {% set opened = data_parity_cache_live(m, '(' ~ m['pexpr'] ~ " >= date '" ~ cutoff ~ "' or " ~ m['pexpr'] ~ ' is null)') %}
      {% for r in opened['rows'] %}{% do parts.update({r[0]: [r[1], r[2], r[3]]}) %}{% endfor %}
      {% do notes.append('NOTE|' ~ m['name'] ~ '|cutoff=' ~ cutoff ~ '|from_cache=' ~ plan['valid'] | length ~ '|live_open=' ~ opened['rows'] | length ~ '|live_closed_not_trusted=' ~ plan['todo'] | length ~ ' (missing=' ~ plan['missing'] | length ~ ', stale=' ~ plan['stale'] | length ~ ')') %}
      {% if plan['todo'] %}{% do notes.append('NOTE|' ~ m['name'] ~ '|closed partitions scanned live because the cache cannot be trusted for them: ' ~ data_parity_cache_brief(plan['todo'])) %}{% endif %}
    {% else %}
      {% set whole = data_parity_cache_live(m, none) %}
      {% do notes.append('NOTE|' ~ m['name'] ~ '|no partition column: full live scan') %}
    {% endif %}

    {% set tr = namespace(n=0, h1=0, h2=0) %}
    {% if m['pexpr'] %}
      {% for k, v in parts.items() %}{% set tr.n = tr.n + v[0] %}{% set tr.h1 = tr.h1 + v[1] %}{% set tr.h2 = tr.h2 + v[2] %}{% endfor %}
    {% else %}
      {% set w = whole['rows'][0] %}{% set tr.n = w[1] %}{% set tr.h1 = w[2] %}{% set tr.h2 = w[3] %}
    {% endif %}
    {% do out.append('PARITY|## ' ~ m['name']) %}
    {% do out.append('NOTE|' ~ m['name'] ~ '|excluded=' ~ (m['excluded'] if m['excluded'] else 'none')) %}
    {% do out.append('PARITY|cols|' ~ m['cols_line']) %}
    {# Mirror the lines data_parity_reconcile prints between cols| and table| (pk, no-partition note), when it is installed. #}
    {% if data_parity_rc_pk is defined %}
      {% set pk = data_parity_rc_pk(m['node'], m['meta']) %}
      {% do out.append('PARITY|pk|' ~ (pk | join(',') if pk | length > 0 else 'none')) %}
      {% if not m['pexpr'] %}{% do out.append('PARITY|note|no partition column: table line only, buckets are taken table-wide (partition value *)') %}{% endif %}
    {% endif %}
    {% do out.append('PARITY|table|rows=' ~ tr.n ~ '|h1=' ~ tr.h1 ~ '|h2=' ~ tr.h2) %}
    {% for k in parts.keys() | list | sort %}
      {% do out.append('PARITY|part|' ~ k ~ '|rows=' ~ parts[k][0] ~ '|h1=' ~ parts[k][1] ~ '|h2=' ~ parts[k][2]) %}
    {% endfor %}
    {% for n in notes %}{% do out.append(n) %}{% endfor %}
  {% endfor %}
  {{ log(out | join('\n'), info=true) }}
{% endmacro %}

{# ---------------------------------------------------------------- 4. verify #}

{# Recompute `sample` random cached (trusted, closed as of cutoff) partitions live and compare with the cache. #}
{% macro data_parity_cache_verify(only=none, sample=5, schema=none, fail_on_stale=true, lag_days=3, reference_date=none) %}
  {% if not execute %}{{ return('') }}{% endif %}
  {% set fail_on_stale = data_parity_cfg_bool(fail_on_stale, 'fail_on_stale') %}
  {% set schema = data_parity_cache_resolve_schema(schema, true) %}
  {% set cutoff = data_parity_cache_cutoff(reference_date, lag_days) %}
  {% set version = data_parity_cache_version() %}
  {% set out = [] %}
  {% set bad = namespace(n=0) %}
  {% for m in data_parity_cache_models(only) %}
    {% if m['pexpr'] %}
      {% set tbl = adapter.get_relation(database=target.database, schema=schema, identifier='data_parity_cache') %}
      {% if tbl is none %}
        {% do out.append('NOTE|verify|' ~ m['name'] ~ '|no cache table, nothing to verify') %}
      {% else %}
        {% set sql = "select partition_value, rows_n, h1_sum, h2_sum from " ~ data_parity_cache_fqn(schema) ~ " where model = '" ~ m['name'] ~ "' and schema_fp = '" ~ m['schema_fp'] ~ "' and version = '" ~ version ~ "' and partition_value < '" ~ cutoff ~ "' order by " ~ data_parity_cache_random() ~ " limit " ~ (sample | int) %}
        {% set picked = {} %}
        {% for r in run_query(sql).rows %}{% do picked.update({r[0] | string: [r[1] | int, r[2] | int, r[3] | int]}) %}{% endfor %}
        {% if picked | length == 0 %}
          {% do out.append('NOTE|verify|' ~ m['name'] ~ '|no trusted cached partition to verify') %}
        {% else %}
          {% set live = {} %}
          {% set scan = data_parity_cache_live(m, m['pexpr'] ~ ' in (' ~ data_parity_cache_date_list(picked.keys() | list | sort) ~ ')') %}
          {% for r in scan['rows'] %}{% do live.update({r[0]: [r[1], r[2], r[3]]}) %}{% endfor %}
          {% for p in picked.keys() | list | sort %}
            {% set c = picked[p] %}
            {% set l = live.get(p, [0, 0, 0]) %}
            {% if c == l %}
              {% do out.append('VERIFY|OK|' ~ m['name'] ~ '|' ~ p ~ '|rows=' ~ c[0]) %}
            {% else %}
              {% set bad.n = bad.n + 1 %}
              {% do out.append('VERIFY|STALE|' ~ m['name'] ~ '|' ~ p ~ '|cached rows=' ~ c[0] ~ ' h1=' ~ c[1] ~ ' h2=' ~ c[2] ~ '|live rows=' ~ l[0] ~ ' h1=' ~ l[1] ~ ' h2=' ~ l[2]) %}
            {% endif %}
          {% endfor %}
        {% endif %}
      {% endif %}
    {% endif %}
  {% endfor %}
  {% do out.append('NOTE|verify|stale_partitions=' ~ bad.n) %}
  {{ log(out | join('\n'), info=true) }}
  {% if bad.n > 0 and fail_on_stale %}
    {% do exceptions.raise_compiler_error('data_parity cache verify: ' ~ bad.n ~ ' cached partition(s) no longer match the live data (rewritten or late data). Bump var data_parity_cache_version and refresh, or delete these rows from the cache.') %}
  {% endif %}
{% endmacro %}
