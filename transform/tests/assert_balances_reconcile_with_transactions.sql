-- The last balance of every account must equal its opening balance plus the sum of its transactions.

with last_balance as (

    select account_no, balance_nok
    from {{ ref('fct_account_balance_daily') }}
    qualify row_number() over (partition by account_no order by balance_date desc) = 1

),

flows as (

    select account_no, sum(amount_nok) as net_flow_nok
    from {{ ref('fct_transactions') }}
    group by 1

)

select
    a.account_no,
    a.opening_balance,
    coalesce(f.net_flow_nok, 0) as net_flow_nok,
    l.balance_nok
from {{ ref('dim_account') }} a
left join flows f on f.account_no = a.account_no
left join last_balance l on l.account_no = a.account_no
where abs(a.opening_balance + coalesce(f.net_flow_nok, 0) - coalesce(l.balance_nok, a.opening_balance)) > 0.005
