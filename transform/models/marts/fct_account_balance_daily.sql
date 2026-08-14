{{ config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key='balance_date',
    on_schema_change='append_new_columns',
    post_hook=[
        "{{ drop_row_access_policies() }}",
        "{{ add_row_access_policy('CUSTOMER_REGION_POLICY', 'customer_id') }}"
    ]
) }}

-- Periodic snapshot: the end-of-day balance of every open account for every
-- day. Balances are cumulative, so an incremental run recomputes every day
-- from the window start onwards, starting from the balance stored for the
-- day before the window. The window follows the strategy under test.

with bounds as (

    select
        {% if is_incremental() %}
        {{ window_start_date('balance_date') }}
        {% else %}
        cast('{{ var("calendar_start") }}' as date)
        {% endif %}                                   as window_start,
        {{ as_of_date_expr() }}                       as window_end

),

days as (

    select d.date_day as balance_date
    from {{ ref('dim_date') }} d
    cross join bounds b
    where d.date_day >= b.window_start
      and d.date_day <= b.window_end

),

accounts as (

    select account_no, customer_id, open_date, close_date, opening_balance
    from {{ ref('dim_account') }}

),

spine as (

    select a.account_no, a.customer_id, a.opening_balance, d.balance_date
    from accounts a
    join days d
      on d.balance_date >= a.open_date
     and (a.close_date is null or d.balance_date <= a.close_date)

),

nets as (

    select f.account_no, f.event_date, sum(f.amount_nok) as net_flow_nok, count(*) as txn_count
    from {{ ref('fct_transactions') }} f
    cross join bounds b
    where f.event_date >= b.window_start
      and f.event_date <= b.window_end
    group by 1, 2

),

base as (

    {% if is_incremental() %}
    select t.account_no, t.balance_nok as base_balance
    from {{ this }} t
    cross join bounds b
    where t.balance_date = {{ dbt.dateadd('day', -1, 'b.window_start') }}
    {% else %}
    select account_no, cast(null as decimal(18, 2)) as base_balance
    from accounts
    where 1 = 0
    {% endif %}

)

select
    s.account_no,
    s.customer_id,
    s.balance_date,
    cast(
        coalesce(b.base_balance, s.opening_balance)
        + coalesce(sum(n.net_flow_nok) over (partition by s.account_no order by s.balance_date rows unbounded preceding), 0)
        as decimal(18, 2)
    )                                                   as balance_nok,
    cast(coalesce(n.net_flow_nok, 0) as decimal(18, 2)) as net_flow_nok,
    coalesce(n.txn_count, 0)                            as txn_count
from spine s
left join base b on b.account_no = s.account_no
left join nets n on n.account_no = s.account_no and n.event_date = s.balance_date
