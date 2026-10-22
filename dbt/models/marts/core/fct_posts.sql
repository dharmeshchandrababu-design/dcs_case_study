-- Fact table. Grain: one relevant post that is not clearly outside Europe.
-- No author dimension on purpose: author_hash is kept only to count distinct authors.
select
    post_id,
    created_date                              as date_key,
    theme_key,
    network_key,
    network_raw,
    author_hash,
    lang,
    region_tag,
    sentiment,
    feedback_type,
    severity,
    severity_ai,
    label_confidence,
    is_low_confidence,
    model_version,
    follower_count,
    engagement,
    view_count,
    case when sentiment in ('negative', 'mixed') then 1 else 0 end as is_negative,
    case when sentiment = 'positive' then 1 else 0 end             as is_positive,
    case when feedback_type = 'request' then 1 else 0 end          as is_request,
    case when severity <> 'none' then 1 else 0 end                 as is_serious,
    text,
    text_en
from {{ ref('int_posts_enriched') }}
