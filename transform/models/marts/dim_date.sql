{{ config(materialized='table') }}

-- One row per calendar day over the whole period the warehouse covers.

with spine as (

    {{ dbt_utils.date_spine(
        datepart="day",
        start_date="cast('" ~ var('calendar_start') ~ "' as date)",
        end_date="cast('" ~ var('calendar_end') ~ "' as date)"
    ) }}

),

days as (

    select cast(date_day as date) as date_day from spine

)

select
    date_day,
    cast(extract(year from date_day) as integer)                       as year,
    cast(extract(quarter from date_day) as integer)                    as quarter,
    cast(extract(month from date_day) as integer)                      as month,
    cast({{ dbt.date_trunc('month', 'date_day') }} as date)            as month_start,
    {{ year_month('date_day') }}                                       as year_month,
    cast(extract(day from date_day) as integer)                        as day_of_month,
    cast({{ iso_day_of_week('date_day') }} as integer)                 as iso_day_of_week,
    cast({{ iso_day_of_week('date_day') }} as integer) >= 6            as is_weekend
from days
