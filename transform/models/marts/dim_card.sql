{{ config(materialized='table') }}

-- Card dimension, type 1.

select
    card_id,
    account_no,
    masked_pan,
    issued_date,
    expiry_date,
    status,
    updated_at as source_updated_at
from {{ ref('int_cards_current') }}
