{{ config(materialized='view') }}

-- The newest delivered version of every customer. This is what the SCD2 snapshot watches.

select *
from {{ ref('stg_customers') }}
qualify row_number() over (partition by customer_id order by updated_at desc, loaded_at desc) = 1
