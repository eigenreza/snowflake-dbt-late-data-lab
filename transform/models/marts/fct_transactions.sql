{{ config(
    materialized='incremental',
    incremental_strategy=fact_incremental_strategy(),
    unique_key=fact_unique_key(),
    on_schema_change='append_new_columns',
    post_hook=[
        "{{ drop_row_access_policies() }}",
        "{{ add_row_access_policy('CUSTOMER_REGION_POLICY', 'customer_id') }}"
    ]
) }}

-- One row per transaction, in its latest delivered state, with the NOK value
-- at the booking date's rate and the customer version that applied that day.
--
-- The incremental behaviour is the experiment's variable. On an incremental
-- run only transactions booked on or after the window start are read:
--   append_only    the day after the newest booking date already loaded
--   merge          W days before it, upserting by transaction id
--   delete_insert  W days before it, replacing whole booking dates

with versions as (

    select *
    from {{ ref('stg_transactions') }}
    {% if is_incremental() %}
    where event_date >= {{ window_start_date('event_date') }}
    {% endif %}

),

latest as (

    select *
    from versions
    qualify row_number() over (partition by txn_id order by updated_at desc, loaded_at desc) = 1

),

accounts as (

    select account_no, customer_id from {{ ref('dim_account') }}

),

customers as (

    select customer_sk, customer_id, valid_from, valid_to from {{ ref('dim_customer') }}

),

fx as (

    select rate_date, currency, rate_nok from {{ ref('int_fx_rates_daily') }}

)

select
    t.txn_id,
    t.account_no,
    a.customer_id,
    c.customer_sk,
    t.card_id,
    t.merchant_id,
    t.txn_type,
    t.event_date,
    t.amount,
    t.currency,
    cast(coalesce(f.rate_nok, 1) as decimal(18, 6))                                   as fx_rate_nok,
    cast(round(t.amount * cast(coalesce(f.rate_nok, 1) as decimal(18, 6)), 2) as decimal(18, 2)) as amount_nok,
    t.status,
    t.reversal_of,
    t.reversal_of is not null                                                         as is_reversal,
    t.updated_at > t.created_at                                                       as is_corrected,
    t.created_at                                                                      as source_created_at,
    t.updated_at                                                                      as source_updated_at,
    t.extract_day,
    t.loaded_at
from latest t
left join accounts a on a.account_no = t.account_no
left join customers c
    on c.customer_id = a.customer_id
   and t.event_date >= c.valid_from
   and t.event_date <  c.valid_to
left join fx f
    on f.currency = t.currency
   and f.rate_date = t.event_date
   and t.currency <> 'NOK'
