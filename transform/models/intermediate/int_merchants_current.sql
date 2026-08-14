{{ config(materialized='view') }}

-- The newest delivered version of every merchant.

select *
from {{ ref('stg_merchants') }}
qualify row_number() over (partition by merchant_id order by updated_at desc, loaded_at desc) = 1
