{#
  Snowflake-only session and security hooks. Each macro renders nothing on
  DuckDB, and dbt skips hooks that render to an empty string.
#}

{# Result cache off, so that repeated experiment runs do real work #}
{% macro disable_result_cache() -%}
    {%- if target.type == 'snowflake' -%}
        alter session set use_cached_result = false
    {%- endif -%}
{%- endmacro %}

{# Query tag naming the strategy and cycle, read back from query history for the cost figures #}
{% macro set_run_query_tag() -%}
    {%- if target.type == 'snowflake' -%}
        alter session set query_tag = '{{ var("query_tag", "late_data_lab") }}'
    {%- endif -%}
{%- endmacro %}

{# Row access policies: detach whatever is attached, then attach the one for this table #}
{% macro drop_row_access_policies() -%}
    {%- if target.type == 'snowflake' -%}
        alter table {{ this }} drop all row access policies
    {%- endif -%}
{%- endmacro %}

{% macro add_row_access_policy(policy, column) -%}
    {%- if target.type == 'snowflake' -%}
        alter table {{ this }} add row access policy {{ target.database }}.marts.{{ policy }} on ({{ column }})
    {%- endif -%}
{%- endmacro %}
