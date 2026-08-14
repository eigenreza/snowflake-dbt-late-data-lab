{#
  Parsing helpers for the legacy extract format. Each one is dispatched per
  adapter so the models stay portable: DuckDB and Snowflake spell date
  parsing differently, everything else is plain SQL.
#}

{% macro parse_legacy_date(column) -%}
    {{ return(adapter.dispatch('parse_legacy_date', 'late_data_lab')(column)) }}
{%- endmacro %}

{% macro default__parse_legacy_date(column) -%}
    try_to_date(nullif(trim({{ column }}), ''), 'DDMMYYYY')
{%- endmacro %}

{% macro duckdb__parse_legacy_date(column) -%}
    cast(try_strptime(nullif(trim({{ column }}), ''), '%d%m%Y') as date)
{%- endmacro %}


{% macro parse_legacy_timestamp(column) -%}
    {{ return(adapter.dispatch('parse_legacy_timestamp', 'late_data_lab')(column)) }}
{%- endmacro %}

{% macro default__parse_legacy_timestamp(column) -%}
    try_to_timestamp_ntz(nullif(trim({{ column }}), ''), 'DDMMYYYY HH24:MI:SS')
{%- endmacro %}

{% macro duckdb__parse_legacy_timestamp(column) -%}
    try_strptime(nullif(trim({{ column }}), ''), '%d%m%Y %H:%M:%S')
{%- endmacro %}


{# Decimal comma to a number: "-1234,50" becomes -1234.50 #}
{% macro parse_decimal_comma(column, precision=18, scale=2) -%}
    try_cast(replace(nullif(trim({{ column }}), ''), ',', '.') as decimal({{ precision }}, {{ scale }}))
{%- endmacro %}


{# Codes typed by hand: trimmed, upper case, empty becomes null #}
{% macro clean_code(column) -%}
    nullif(upper(trim({{ column }})), '')
{%- endmacro %}


{#
  Names typed by hand in whatever case the operator used, restored to
  title case with the small Norwegian words kept lower ("Mo i Rana",
  "Møre og Romsdal").
#}
{% macro title_case(column) -%}
    replace(replace({{ adapter.dispatch('title_case_words', 'late_data_lab')(column) }}, ' I ', ' i '), ' Og ', ' og ')
{%- endmacro %}

{% macro default__title_case_words(column) -%}
    initcap(lower(trim({{ column }})))
{%- endmacro %}

{% macro duckdb__title_case_words(column) -%}
    array_to_string(
        list_transform(string_split(lower(trim({{ column }})), ' '), lambda w: upper(w[1]) || w[2:]),
        ' '
    )
{%- endmacro %}


{# Surrogate key: a stable hash of the given columns, the same on both engines #}
{% macro surrogate_key(columns) -%}
    {{ dbt_utils.generate_surrogate_key(columns) }}
{%- endmacro %}
