{{ config(materialized='view') }}

-- Published Norges Bank rates: one row per currency and publication date, in NOK per unit.

with source as (

    select * from {{ source('raw', 'fx_rates') }}

),

typed as (

    select
        try_cast(rate_date as date)                       as rate_date,
        {{ clean_code('currency') }}                      as currency,
        try_cast(rate_nok as decimal(18, 6))              as rate_nok,
        _file_name                                        as source_file,
        _loaded_at                                        as loaded_at
    from source

)

select *
from typed
where rate_date is not null and currency is not null and rate_nok is not null
qualify row_number() over (partition by rate_date, currency order by loaded_at desc) = 1
