-- A converted amount must sit between the lowest and highest rate ever published
-- for its currency, and NOK amounts must be unchanged.

with rate_range as (

    select currency, min(rate_nok) as min_rate, max(rate_nok) as max_rate
    from {{ ref('stg_fx_rates') }}
    group by 1

)

select f.txn_id, f.currency, f.amount, f.amount_nok, f.fx_rate_nok
from {{ ref('fct_transactions') }} f
left join rate_range r on r.currency = f.currency
where (f.currency = 'NOK' and f.amount_nok <> f.amount)
   or (f.currency <> 'NOK' and (
            r.currency is null
         or f.fx_rate_nok < r.min_rate or f.fx_rate_nok > r.max_rate
         or abs(f.amount_nok) < abs(f.amount) * r.min_rate - 0.01
         or abs(f.amount_nok) > abs(f.amount) * r.max_rate + 0.01
      ))
