{{ config(materialized='view') }}

-- The newest delivered version of every account.

select *
from {{ ref('stg_accounts') }}
qualify row_number() over (partition by account_no order by updated_at desc, loaded_at desc) = 1
