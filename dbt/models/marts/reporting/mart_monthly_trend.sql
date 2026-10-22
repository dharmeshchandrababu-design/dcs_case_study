-- Is a problem DCS can act on getting better or worse? One row per month and theme.
select
    d.month_start,
    t.theme,
    count(*)                         as posts,
    round(avg(f.is_negative), 2)     as negative_share,
    count(*) < 10                    as is_small_group
from {{ ref('fct_posts') }} f
join {{ ref('dim_date') }} d using (date_key)
join {{ ref('dim_theme') }} t using (theme_key)
where t.dcs_lever in ('yes', 'partly')
group by d.month_start, t.theme
order by d.month_start, t.theme
