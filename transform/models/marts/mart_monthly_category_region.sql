{{ config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key='report_month',
    on_schema_change='append_new_columns',
    post_hook=[
        "{{ drop_row_access_policies() }}",
        "{{ add_row_access_policy('REGION_POLICY', 'region') }}"
    ]
) }}

-- Monthly transaction value and count by merchant category and customer
-- region, in NOK: the rewrite of the old nightly reporting job, with the same
-- grain and columns. Card purchases and bill payments only; spend is positive;
-- the region is the customer's current region, as in the old job.
--
-- Like the old job, an incremental run replaces whole months, starting from
-- the month that contains the window start.

with bounds as (

    select
        {% if is_incremental() %}
        cast({{ dbt.date_trunc('month', window_start_date('report_month')) }} as date)
        {% else %}
        cast('{{ var("calendar_start") }}' as date)
        {% endif %} as window_start_month

),

transactions as (

    select f.txn_id, f.event_date, f.amount_nok, f.merchant_id, f.customer_id
    from {{ ref('fct_transactions') }} f
    cross join bounds b
    where f.txn_type in ('CARD_PURCHASE', 'BILL_PAYMENT')
      and f.event_date >= b.window_start_month

),

current_customers as (

    select customer_id, region
    from {{ ref('dim_customer') }}
    where is_current

)

select
    cast({{ dbt.date_trunc('month', 't.event_date') }} as date)   as report_month,
    m.category_code,
    c.region,
    cast(sum(-1 * t.amount_nok) as decimal(18, 2))                as value_nok,
    count(*)                                                       as txn_count
from transactions t
join {{ ref('dim_merchant') }} m on m.merchant_id = t.merchant_id
join current_customers c on c.customer_id = t.customer_id
group by 1, 2, 3
