{{ config(materialized='table') }}

-- Account dimension, type 1: the current state of every account.

select
    a.account_no,
    a.customer_id,
    a.account_type,
    a.open_date,
    a.close_date,
    a.status,
    a.opening_balance,
    a.credit_limit,
    a.updated_at as source_updated_at
from {{ ref('int_accounts_current') }} a
