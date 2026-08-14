{{ config(materialized='view') }}

-- One row per delivered account version. An account appears again when it is closed.

with source as (

    select * from {{ source('raw', 'accounts') }}

),

typed as (

    select
        nullif(trim(ACCT_NO), '')                         as account_no,
        try_cast(CUST_ID as integer)                      as customer_id,
        {{ clean_code('ACCT_TYP') }}                      as account_type,
        {{ parse_legacy_date('OPEN_DT') }}                as open_date,
        {{ parse_legacy_date('CLOSE_DT') }}               as close_date,
        {{ clean_code('STAT') }}                          as status,
        {{ parse_decimal_comma('OPEN_BAL') }}             as opening_balance,
        coalesce({{ parse_decimal_comma('CR_LIM') }}, 0)  as credit_limit,
        {{ parse_legacy_timestamp('CRT_TS') }}            as created_at,
        {{ parse_legacy_timestamp('UPD_TS') }}            as updated_at,
        _file_name                                        as source_file,
        _extract_day                                      as extract_day,
        _loaded_at                                        as loaded_at
    from source

)

select *
from typed
where account_no is not null
qualify row_number() over (partition by account_no, updated_at order by loaded_at desc, source_file) = 1
