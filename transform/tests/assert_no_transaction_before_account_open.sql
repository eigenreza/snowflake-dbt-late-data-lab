-- No transaction may be booked before its account was opened.

select f.txn_id, f.account_no, f.event_date, a.open_date
from {{ ref('fct_transactions') }} f
join {{ ref('dim_account') }} a on a.account_no = f.account_no
where f.event_date < a.open_date
