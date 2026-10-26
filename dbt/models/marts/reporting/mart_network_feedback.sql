-- What is each charging network praised and criticised for? One row per network, theme and sentiment group.
-- Read with mart_network_scorecard: the scorecard says how much, this mart says why.
with posts as (
    select
        f.network_key,
        f.theme_key,
        case when f.is_positive = 1 then 'praised'
             when f.is_negative = 1 then 'criticised'
             else 'neutral' end as feedback_group,
        f.author_hash,
        f.engagement
    from {{ ref('fct_posts') }} f
    where f.network_key not in ('none', 'unknown')
)
select
    n.network,
    n.network_role,
    p.feedback_group,
    t.theme,
    t.subcategory,
    count(*)                                        as posts,
    count(distinct p.author_hash)                   as authors,
    round(count(*) / sum(count(*)) over (partition by n.network, p.feedback_group), 2)
                                                    as share_within_group,
    sum(p.engagement)                               as engagement
from posts p
join {{ ref('dim_network') }} n using (network_key)
join {{ ref('dim_theme') }} t using (theme_key)
group by n.network, n.network_role, p.feedback_group, t.theme, t.subcategory
order by n.network, p.feedback_group, posts desc
