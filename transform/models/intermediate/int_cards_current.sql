{{ config(materialized='view') }}

-- The newest delivered version of every card.

select *
from {{ ref('stg_cards') }}
qualify row_number() over (partition by card_id order by updated_at desc, loaded_at desc) = 1
