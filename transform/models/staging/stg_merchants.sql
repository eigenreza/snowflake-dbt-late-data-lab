{{ config(materialized='view') }}

-- One row per delivered merchant version. The category name comes from the
-- seed keyed by category code; the delivered name is only a fallback.

with source as (

    select * from {{ source('raw', 'merchants') }}

),

typed as (

    select
        try_cast(MERCH_ID as integer)                     as merchant_id,
        nullif({{ title_case('MERCH_NM') }}, '')          as merchant_name,
        nullif(trim(CAT_CD), '')                          as category_code,
        nullif({{ title_case('CAT_NM') }}, '')            as delivered_category_name,
        {{ clean_code('CTRY') }}                          as country_code,
        nullif(trim(CITY), '')                            as city,
        {{ parse_legacy_timestamp('CRT_TS') }}            as created_at,
        {{ parse_legacy_timestamp('UPD_TS') }}            as updated_at,
        _file_name                                        as source_file,
        _extract_day                                      as extract_day,
        _loaded_at                                        as loaded_at
    from source

),

named as (

    select
        t.merchant_id,
        t.merchant_name,
        t.category_code,
        coalesce(c.category_name, t.delivered_category_name) as category_name,
        t.country_code,
        t.city,
        t.created_at,
        t.updated_at,
        t.source_file,
        t.extract_day,
        t.loaded_at
    from typed t
    left join {{ ref('merchant_categories') }} c on c.category_code = t.category_code

)

select *
from named
where merchant_id is not null
qualify row_number() over (partition by merchant_id, updated_at order by loaded_at desc, source_file) = 1
