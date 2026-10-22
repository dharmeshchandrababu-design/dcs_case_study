-- Which problems are frequent, negative and fixable by DCS? One row per theme and subcategory.
-- priority_score = negative posts x lever weight (yes 1.0, partly 0.6, no 0.2) x engagement boost.
-- Note: search volumes were capped per query, so compare shares, not raw volumes across searches.
with agg as (
    select
        theme_key,
        count(*)                          as posts,
        count(distinct author_hash)       as authors,
        sum(is_negative)                  as negative_posts,
        sum(is_serious)                   as serious_posts,
        sum(is_request)                   as requests,
        avg(engagement)                   as avg_engagement
    from {{ ref('fct_posts') }}
    group by theme_key
)
select
    t.theme,
    t.subcategory,
    t.owner_team,
    t.dcs_lever,
    a.posts,
    a.authors,
    a.negative_posts,
    round(a.negative_posts / a.posts, 2)                       as negative_share,
    a.serious_posts,
    a.requests,
    round(a.avg_engagement, 1)                                 as avg_engagement,
    round(a.negative_posts * t.lever_weight * (1 + ln(1 + a.avg_engagement)), 1) as priority_score,
    a.posts < 20                                               as is_small_group
from agg a
join {{ ref('dim_theme') }} t using (theme_key)
where t.theme not in ('other')
order by priority_score desc
