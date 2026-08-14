{#
  The loading strategy under test, chosen with the load_strategy variable:

    full_refresh   rebuild everything (run with --full-refresh)
    append_only    insert rows newer than the newest row already loaded
    merge          upsert by business key within a lookback window
    delete_insert  replace whole event dates within a lookback window

  lookback_days (W) sets the window for merge and delete_insert.
#}

{% macro load_strategy() -%}
    {{ return(var('load_strategy', 'full_refresh')) }}
{%- endmacro %}

{% macro lookback_days() -%}
    {{ return(var('lookback_days', 0) | int) }}
{%- endmacro %}

{# dbt incremental strategy for the transaction fact #}
{% macro fact_incremental_strategy() -%}
    {%- set s = load_strategy() -%}
    {%- if s == 'append_only' -%}{{ return('append') }}
    {%- elif s == 'merge' -%}{{ return('merge') }}
    {%- else -%}{{ return('delete+insert') }}
    {%- endif -%}
{%- endmacro %}

{# merge upserts by transaction id; delete_insert replaces whole booking dates #}
{% macro fact_unique_key() -%}
    {%- if load_strategy() == 'delete_insert' -%}{{ return('event_date') }}
    {%- else -%}{{ return('txn_id') }}
    {%- endif -%}
{%- endmacro %}

{#
  First date an incremental run processes, as a scalar subquery on the
  model's own table. append_only starts the day after the newest row;
  the windowed strategies go back W days from it.
#}
{% macro window_start_date(date_column) -%}
    {%- if load_strategy() == 'append_only' -%}
        (select {{ dbt.dateadd('day', 1, 'max(' ~ date_column ~ ')') }} from {{ this }})
    {%- else -%}
        (select {{ dbt.dateadd('day', -lookback_days(), 'max(' ~ date_column ~ ')') }} from {{ this }})
    {%- endif -%}
{%- endmacro %}

{# Last day of the replay cycle being built, or the newest booking date in staging #}
{% macro as_of_date_expr() -%}
    {%- if var('as_of_date', none) -%}
        cast('{{ var("as_of_date") }}' as date)
    {%- else -%}
        (select max(event_date) from {{ ref('stg_transactions') }})
    {%- endif -%}
{%- endmacro %}
