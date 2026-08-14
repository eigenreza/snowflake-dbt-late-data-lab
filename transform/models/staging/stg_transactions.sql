{{ config(materialized='view') }}

-- One row per delivered transaction version. A corrected transaction keeps its
-- id and arrives again with a later updated_at; the fact picks the latest.
-- Duplicate lines (same id and update time) are collapsed here.

with source as (

    select * from {{ source('raw', 'transactions') }}

),

typed as (

    select
        try_cast(TXN_ID as bigint)                        as txn_id,
        nullif(trim(ACCT_NO), '')                         as account_no,
        nullif(trim(CARD_ID), '')                         as card_id,
        try_cast(MERCH_ID as integer)                     as merchant_id,
        {{ clean_code('TXN_TYP') }}                       as txn_type,
        {{ parse_legacy_date('BOOK_DT') }}                as event_date,
        {{ parse_decimal_comma('AMT') }}                  as amount,
        {{ clean_code('CCY') }}                           as currency,
        {{ clean_code('STAT') }}                          as status,
        try_cast(REV_OF as bigint)                        as reversal_of,
        {{ parse_legacy_timestamp('CRT_TS') }}            as created_at,
        {{ parse_legacy_timestamp('UPD_TS') }}            as updated_at,
        _file_name                                        as source_file,
        _extract_day                                      as extract_day,
        _loaded_at                                        as loaded_at
    from source

)

select *
from typed
where txn_id is not null
qualify row_number() over (partition by txn_id, updated_at order by loaded_at desc, source_file) = 1
