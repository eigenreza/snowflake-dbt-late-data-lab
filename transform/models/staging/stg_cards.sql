{{ config(materialized='view') }}

-- One row per delivered card version.

with source as (

    select * from {{ source('raw', 'cards') }}

),

typed as (

    select
        nullif(trim(CARD_ID), '')                         as card_id,
        nullif(trim(ACCT_NO), '')                         as account_no,
        nullif(trim(PAN_MASK), '')                        as masked_pan,
        {{ parse_legacy_date('ISSUE_DT') }}               as issued_date,
        {{ parse_legacy_date('EXP_DT') }}                 as expiry_date,
        {{ clean_code('STAT') }}                          as status,
        {{ parse_legacy_timestamp('CRT_TS') }}            as created_at,
        {{ parse_legacy_timestamp('UPD_TS') }}            as updated_at,
        _file_name                                        as source_file,
        _extract_day                                      as extract_day,
        _loaded_at                                        as loaded_at
    from source

)

select *
from typed
where card_id is not null
qualify row_number() over (partition by card_id, updated_at order by loaded_at desc, source_file) = 1
