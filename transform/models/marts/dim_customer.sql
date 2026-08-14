{{ config(
    materialized='table',
    post_hook=[
        "{{ drop_row_access_policies() }}",
        "{{ add_row_access_policy('REGION_POLICY', 'region') }}"
    ]
) }}

-- Customer dimension, SCD type 2: one row per customer version with the date
-- range it applied. The first version is valid from the beginning of time so
-- that history before the first extract still finds a customer row.

with versions as (

    select
        s.*,
        row_number() over (partition by customer_id order by dbt_valid_from) as version_no,
        lead(cast(dbt_valid_from as date)) over (partition by customer_id order by dbt_valid_from) as next_valid_from
    from {{ ref('snap_customers') }} s

)

select
    {{ surrogate_key(['v.customer_id', 'v.dbt_valid_from']) }}         as customer_sk,
    v.customer_id,
    v.customer_no,
    v.first_name,
    v.last_name,
    v.first_name || ' ' || v.last_name                                  as full_name,
    v.birth_date,
    v.segment,
    v.region,
    r.area,
    v.street,
    v.postal_code,
    v.city,
    v.customer_since,
    case when v.version_no = 1 then cast('1900-01-01' as date) else cast(v.dbt_valid_from as date) end as valid_from,
    coalesce(v.next_valid_from, cast('9999-12-31' as date))             as valid_to,
    v.dbt_valid_to is null                                              as is_current,
    v.version_no,
    v.dbt_valid_from                                                    as version_started_at,
    v.dbt_valid_to                                                      as version_ended_at
from versions v
left join {{ ref('regions') }} r on r.region = v.region
