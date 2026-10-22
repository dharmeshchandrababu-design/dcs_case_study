-- Per network: how drivers talk about it, and share of voice among posts that name a network.
-- Read by role: cpo = partner quality, emsp_competitor = rival offers, dcs = our own visibility.
-- Every network in the seed is listed, so a network nobody mentions (posts = 0) is visible too.
with named as (
    select * from {{ ref('fct_posts') }} where network_key <> 'none'
),
counts as (
    select
        network_key,
        count(*)                        as posts,
        count(distinct author_hash)     as authors,
        avg(is_negative)                as negative_share,
        avg(is_positive)                as positive_share
    from named
    group by network_key
),
top_complaint as (
    select network_key, theme_key as top_complaint
    from named
    where is_negative = 1
    group by network_key, theme_key
    qualify row_number() over (partition by network_key order by count(*) desc, theme_key) = 1
)
select
    n.network,
    n.network_role,
    coalesce(c.posts, 0)                                        as posts,
    coalesce(c.authors, 0)                                      as authors,
    round(c.negative_share, 2)                                  as negative_share,
    round(c.positive_share, 2)                                  as positive_share,
    tc.top_complaint,
    round(coalesce(c.posts, 0) / sum(coalesce(c.posts, 0)) over (), 3) as share_of_voice,
    coalesce(c.posts, 0) < 20                                   as is_small_group
from {{ ref('dim_network') }} n
left join counts c using (network_key)
left join top_complaint tc using (network_key)
where n.network_key <> 'none'
order by posts desc, n.network
