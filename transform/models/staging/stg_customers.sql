{{ config(materialized='view') }}

-- One row per delivered customer version: typed, trimmed, names and places in title case.
-- Lines delivered twice (same customer and update time) are collapsed to one.

with source as (

    select * from {{ source('raw', 'customers') }}

),

typed as (

    select
        try_cast(CUST_ID as integer)                      as customer_id,
        nullif(trim(CUST_NO), '')                         as customer_no,
        nullif(trim(FIRST_NM), '')                        as first_name,
        nullif(trim(LAST_NM), '')                         as last_name,
        {{ parse_legacy_date('BIRTH_DT') }}               as birth_date,
        nullif({{ title_case('SEGM') }}, '')              as segment,
        nullif({{ title_case('REGION') }}, '')            as region,
        nullif(trim(STREET), '')                          as street,
        nullif(trim(POST_CD), '')                         as postal_code,
        nullif({{ title_case('CITY') }}, '')              as city,
        {{ parse_legacy_date('CUST_SINCE') }}             as customer_since,
        {{ parse_legacy_timestamp('CRT_TS') }}            as created_at,
        {{ parse_legacy_timestamp('UPD_TS') }}            as updated_at,
        _file_name                                        as source_file,
        _extract_day                                      as extract_day,
        _loaded_at                                        as loaded_at
    from source

)

select *
from typed
where customer_id is not null
qualify row_number() over (partition by customer_id, updated_at order by loaded_at desc, source_file) = 1
