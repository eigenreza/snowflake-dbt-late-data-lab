-- No end-of-day balance may be lower than minus the account's overdraft limit: the core system
-- declines the debit that would take it there, so such a row would mean a load error.

select b.account_no, b.balance_date, b.balance_nok, a.credit_limit
from {{ ref('fct_account_balance_daily') }} b
join {{ ref('dim_account') }} a on a.account_no = b.account_no
where b.balance_nok < -1 * a.credit_limit - 0.005
