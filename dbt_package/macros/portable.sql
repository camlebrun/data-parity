{#
  Helpers that let a plain dbt project (no dual-engine Jinja) be compared across engines.

    data_parity_norm_name(name)            -> lower-cased name, every character outside [a-z0-9_] replaced by `_`
    data_parity_rc_relation(node, base, rel_namespace, relations)   (base = ref(node.name), passed in by the caller)
                                           -> relation read for a model on THIS target
    data_parity_rc_exclusions(node, meta, exclude_columns, exclude_prefixes, no_default_exclude)
                                           -> {cols: [normalized names], prefixes: [normalized prefixes]}

  Nothing here knows a catalog, schema, dataset or project name: they only come from the arguments.
#}

{# `a/b` (Trino) and `a_b` (BigQuery) are the same column. Pure Jinja on purpose (same result in Jinja2 and MiniJinja). #}
{% macro data_parity_norm_name(name) %}
  {%- set ok = 'abcdefghijklmnopqrstuvwxyz0123456789_' -%}
  {%- set ns = namespace(s='') -%}
  {%- for ch in (name | string | lower) -%}
    {%- set ns.s = ns.s ~ (ch if ch in ok else '_') -%}
  {%- endfor -%}
  {{- return(ns.s) -}}
{% endmacro %}

{# Identifier part of a namespace or mapping entry: letters, digits, underscore, hyphen (BigQuery project ids). #}
{% macro data_parity_rc_check_part(part, what) %}
  {%- if not modules.re.match('^[A-Za-z0-9_-]+$', part | string) -%}
    {{ exceptions.raise_compiler_error("data_parity: " ~ what ~ ": invalid name '" ~ part ~ "' (letters, digits, underscore and hyphen only, no quote, backslash or space)") }}
  {%- endif -%}
{% endmacro %}

{# Key that matches a model across the two sides: meta.data_parity.compare_as, else the model name.
   It is what the `## ` header, the NOTE lines and `only` use. #}
{% macro data_parity_rc_key(node, meta) %}
  {%- set k = meta.get('compare_as') if meta is mapping else none -%}
  {%- if k is none or k == '' -%}{{ return(node.name) }}{%- endif -%}
  {%- do data_parity_rc_check_part(k, node.name ~ ': meta.data_parity.compare_as') -%}
  {{- return(k | string) -}}
{% endmacro %}

{# NB: the argument is not called `namespace`: that would hide Jinja's namespace() inside the calling macro.
   Relation read for `node` on this target, in this order:
     1. relations[node.name] (or relations[<its match key>])   '[database.]schema.table'
     2. namespace              '[database.]schema', table = identifier of the model's own ref()
     3. ref(node.name)
   A one-part namespace / two-part mapping keeps the database of the model's own relation. #}
{% macro data_parity_rc_relation(node, base, rel_namespace, relations) %}
  {%- set rels = relations if relations is mapping else {} -%}
  {%- set meta = (node.config.meta or {}).get('data_parity') -%}
  {%- set mapped = rels.get(node.name) -%}
  {%- if mapped is none -%}{%- set mapped = rels.get(data_parity_rc_key(node, meta)) -%}{%- endif -%}
  {%- if mapped is none and not rel_namespace -%}{{ return(base) }}{%- endif -%}
  {%- if mapped is not none -%}
    {%- set parts = (mapped | string).split('.') -%}
    {%- if parts | length not in [2, 3] -%}
      {{ exceptions.raise_compiler_error("data_parity: relations." ~ node.name ~ " must be [database.]schema.table, got '" ~ mapped ~ "'") }}
    {%- endif -%}
    {%- for p in parts -%}{%- do data_parity_rc_check_part(p, 'relations.' ~ node.name) -%}{%- endfor -%}
    {%- set database = parts[0] if parts | length == 3 else base.database -%}
    {{- return(api.Relation.create(database=database, schema=parts[-2], identifier=parts[-1])) -}}
  {%- endif -%}
  {%- set parts = (rel_namespace | string).split('.') -%}
  {%- if parts | length not in [1, 2] -%}
    {{ exceptions.raise_compiler_error("data_parity: the namespace (--schema-a / --schema-b) must be [database.]schema, got '" ~ rel_namespace ~ "'") }}
  {%- endif -%}
  {%- for p in parts -%}{%- do data_parity_rc_check_part(p, 'namespace') -%}{%- endfor -%}
  {%- set database = parts[0] if parts | length == 2 else base.database -%}
  {{- return(api.Relation.create(database=database, schema=parts[-1], identifier=base.identifier)) -}}
{% endmacro %}

{# Excluded columns / prefixes of a model: its meta.data_parity (default prefix `meta_`, unless no_default_exclude)
   plus the arguments. exclude_columns entries are `col` (every model) or `model.col` (that model only). #}
{% macro data_parity_rc_exclusions(node, meta, exclude_columns, exclude_prefixes, no_default_exclude) %}
  {%- set cols = [] -%}
  {%- for c in data_parity_cfg_list(meta.get('exclude_columns'), node.name ~ ': meta.data_parity.exclude_columns', []) -%}
    {%- do cols.append(data_parity_norm_name(c) | trim) -%}
  {%- endfor -%}
  {%- for e in data_parity_cfg_list(exclude_columns, 'exclude_columns', []) -%}
    {%- if '.' in e -%}
      {%- set model = e.split('.')[0] -%}
      {%- if model == node.name or model == data_parity_rc_key(node, meta) -%}{%- do cols.append(data_parity_norm_name(e[(model | length) + 1:]) | trim) -%}{%- endif -%}
    {%- else -%}
      {%- do cols.append(data_parity_norm_name(e) | trim) -%}
    {%- endif -%}
  {%- endfor -%}
  {%- set prefixes = [] -%}
  {%- set default_prefixes = [] if no_default_exclude else ['meta_'] -%}
  {%- for p in data_parity_cfg_list(meta.get('exclude_prefixes'), node.name ~ ': meta.data_parity.exclude_prefixes', default_prefixes) -%}
    {%- do prefixes.append(data_parity_norm_name(p) | trim) -%}
  {%- endfor -%}
  {%- for p in data_parity_cfg_list(exclude_prefixes, 'exclude_prefixes', []) -%}
    {%- do prefixes.append(data_parity_norm_name(p) | trim) -%}
  {%- endfor -%}
  {{- return({'cols': cols, 'prefixes': prefixes}) -}}
{% endmacro %}
