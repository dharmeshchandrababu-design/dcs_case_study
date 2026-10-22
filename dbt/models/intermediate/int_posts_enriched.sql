-- Shared join of posts, labels and the network seed. Keeps relevant posts that are not clearly outside Europe.
-- the labeller sometimes returns several names ("tesla, enbw"): use the first one
with labels as (
    select *, trim(string_split(regexp_replace(network_mentioned, '[/;&]| and | und ', ',', 'g'), ',')[1]) as network_first
    from {{ ref('stg_labels') }}
),

networks as (
    select lower(network) as network_key, network, network_role, aliases from {{ ref('networks') }}
)

select
    p.post_id,
    p.created_date,
    p.created_month,
    p.text,
    case when p.lang = 'en' then p.text else l.text_en end        as text_en,
    p.lang,
    p.author_hash,
    p.follower_count,
    p.region_tag,
    p.like_count + p.retweet_count + p.reply_count + p.quote_count as engagement,
    p.view_count,
    l.theme || '/' || l.subcategory                                 as theme_key,
    l.sentiment,
    l.feedback_type,
    l.severity                                                      as severity_ai,
    {{ severity_checked('l.severity', "coalesce(l.text_en, '') || ' ' || p.text") }} as severity,
    l.label_confidence,
    l.label_confidence < 0.5                                        as is_low_confidence,
    l.model_version,
    l.network_mentioned                                             as network_raw,
    case
        when l.network_first in ('none', '') then 'none'
        when n.network_key is not null then n.network_key
        else 'unknown'
    end                                                             as network_key
from {{ ref('stg_posts') }} p
join labels l on l.post_id = p.post_id
-- match the name the labeller returned against the seed (exact name or any alias)
left join networks n
    on n.network_key = l.network_first
    or regexp_matches(l.network_first, '^(' || n.aliases || ')$')
where l.relevant
  and p.region_tag <> 'outside'
qualify row_number() over (partition by p.post_id order by n.network_key) = 1
