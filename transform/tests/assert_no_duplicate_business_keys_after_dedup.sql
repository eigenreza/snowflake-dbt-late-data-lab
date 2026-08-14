-- After staging, a business key may appear only once per source update time,
-- whatever the legacy files delivered.

select 'customers' as entity, cast(customer_id as varchar) as business_key, updated_at, count(*) as rows_found
from {{ ref('stg_customers') }} group by 1, 2, 3 having count(*) > 1

union all

select 'accounts', account_no, updated_at, count(*)
from {{ ref('stg_accounts') }} group by 1, 2, 3 having count(*) > 1

union all

select 'cards', card_id, updated_at, count(*)
from {{ ref('stg_cards') }} group by 1, 2, 3 having count(*) > 1

union all

select 'merchants', cast(merchant_id as varchar), updated_at, count(*)
from {{ ref('stg_merchants') }} group by 1, 2, 3 having count(*) > 1

union all

select 'transactions', cast(txn_id as varchar), updated_at, count(*)
from {{ ref('stg_transactions') }} group by 1, 2, 3 having count(*) > 1
