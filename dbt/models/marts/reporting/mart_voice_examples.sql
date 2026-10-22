-- Drivers' own words: the 3 most engaged complaints or requests per problem, plus every serious post.
-- risk_bucket: dcs_risk (serious and about DCS), partner_risk (serious, about a charging network),
-- market_watch (serious, no network or a rival).
with posts as (
    select
        f.*,
        t.theme, t.subcategory, t.owner_team,
        n.network, n.network_role,
        row_number() over (partition by f.theme_key order by f.engagement desc, f.post_id) as rank_in_theme
    from {{ ref('fct_posts') }} f
    join {{ ref('dim_theme') }} t using (theme_key)
    join {{ ref('dim_network') }} n using (network_key)
    where f.is_negative = 1 or f.is_request = 1 or f.is_serious = 1
)
select
    date_key,
    theme,
    subcategory,
    owner_team,
    sentiment,
    feedback_type,
    severity,
    network,
    network_role,
    case
        when severity = 'none' then null
        when network_role = 'dcs' then 'dcs_risk'
        when network_role in ('cpo', 'both') then 'partner_risk'
        else 'market_watch'
    end as risk_bucket,
    engagement,
    coalesce(text_en, text) as text_en,
    text                    as text_original
from posts
where rank_in_theme <= 3 or is_serious = 1
order by risk_bucket nulls last, theme, engagement desc
