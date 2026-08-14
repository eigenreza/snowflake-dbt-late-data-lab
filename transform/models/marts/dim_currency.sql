{{ config(materialized='table') }}

-- Currencies in the ledger, with the latest published rate for reference.

with latest_rate as (

    select currency, rate_nok, rate_date
    from {{ ref('stg_fx_rates') }}
    qualify row_number() over (partition by currency order by rate_date desc) = 1

)

select
    c.currency_code,
    c.currency_name,
    c.is_home_currency,
    case when c.is_home_currency then cast(1 as decimal(18, 6)) else r.rate_nok end as latest_rate_nok,
    r.rate_date as latest_rate_date
from {{ ref('currencies') }} c
left join latest_rate r on r.currency = c.currency_code
