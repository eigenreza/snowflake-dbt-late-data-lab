{{ config(materialized='table') }}

-- One rate per calendar day and currency. Norges Bank publishes on business
-- days only; weekends and holidays carry the last published rate forward.

with published as (

    select
        rate_date,
        currency,
        rate_nok,
        lead(rate_date) over (partition by currency order by rate_date) as next_rate_date
    from {{ ref('stg_fx_rates') }}

),

calendar as (

    {{ dbt_utils.date_spine(
        datepart="day",
        start_date="cast('" ~ var('calendar_start') ~ "' as date)",
        end_date="cast('" ~ var('calendar_end') ~ "' as date)"
    ) }}

)

select
    cast(c.date_day as date)                          as rate_date,
    p.currency,
    p.rate_nok,
    cast(c.date_day as date) = p.rate_date            as is_published,
    p.rate_date                                       as published_on
from calendar c
join published p
    on cast(c.date_day as date) >= p.rate_date
   and (p.next_rate_date is null or cast(c.date_day as date) < p.next_rate_date)
