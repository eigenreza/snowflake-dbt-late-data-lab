{{ config(materialized='table') }}

-- Merchant dimension, type 1, with the category hierarchy from the seed.

select
    m.merchant_id,
    m.merchant_name,
    m.category_code,
    coalesce(c.category_name, m.category_name)  as category_name,
    c.category_group,
    m.country_code,
    m.country_code = 'NO'                       as is_domestic,
    m.city,
    m.updated_at                                as source_updated_at
from {{ ref('int_merchants_current') }} m
left join {{ ref('merchant_categories') }} c on c.category_code = m.category_code
