-- One row per day in the data range.
with bounds as (
    select min(created_date) as d_from, max(created_date) as d_to from {{ ref('stg_posts') }}
)
select
    cast(d as date)                                as date_key,
    cast(date_trunc('week', d) as date)            as week_start,
    cast(date_trunc('month', d) as date)           as month_start,
    'Q' || cast(quarter(d) as varchar) || ' ' || cast(year(d) as varchar) as quarter_label,
    year(d)                                        as year
from bounds, generate_series(cast(d_from as timestamp), cast(d_to as timestamp), interval 1 day) as t(d)
