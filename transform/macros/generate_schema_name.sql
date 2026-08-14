{#
  Use the configured schema name as-is instead of prefixing it with the
  target schema, so models land in STAGING and MARTS on both targets.

  The experiment harness sets schema_suffix to give every strategy run its
  own copy of the layers on a shared Snowflake database (for example
  MARTS__MERGE_W3). RAW is shared.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- set suffix = var('schema_suffix', '') -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}{{ '__' ~ suffix if suffix else '' }}
    {%- endif -%}
{%- endmacro %}
