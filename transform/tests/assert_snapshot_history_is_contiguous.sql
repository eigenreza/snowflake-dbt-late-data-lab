-- Customer versions must tile the timeline without gaps or overlaps, and exactly one version must be current.

with ranges as (

    select
        customer_id,
        valid_from,
        valid_to,
        is_current,
        lag(valid_to) over (partition by customer_id order by valid_from) as previous_valid_to,
        sum(case when is_current then 1 else 0 end) over (partition by customer_id) as current_versions
    from {{ ref('dim_customer') }}

)

select *
from ranges
where (previous_valid_to is not null and previous_valid_to <> valid_from)
   or current_versions <> 1
   or valid_to < valid_from
