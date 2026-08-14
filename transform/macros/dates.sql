{# Date helpers that differ between DuckDB and Snowflake #}

{% macro iso_day_of_week(column) -%}
    {{ return(adapter.dispatch('iso_day_of_week', 'late_data_lab')(column)) }}
{%- endmacro %}

{% macro default__iso_day_of_week(column) -%}
    dayofweekiso({{ column }})
{%- endmacro %}

{% macro duckdb__iso_day_of_week(column) -%}
    isodow({{ column }})
{%- endmacro %}


{# 'YYYY-MM' label of a date #}
{% macro year_month(column) -%}
    cast(extract(year from {{ column }}) as varchar) || '-' || lpad(cast(extract(month from {{ column }}) as varchar), 2, '0')
{%- endmacro %}
