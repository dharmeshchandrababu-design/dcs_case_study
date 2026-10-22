-- One label per post. If a post has several labels, AI labels win over the keyword baseline,
-- and the newest label wins within the same labeller.
select
    post_id,
    relevant,
    lower(trim(theme))             as theme,
    lower(trim(subcategory))       as subcategory,
    lower(trim(sentiment))         as sentiment,
    lower(trim(feedback_type))     as feedback_type,
    lower(trim(severity))          as severity,
    lower(trim(coalesce(network_mentioned, 'none'))) as network_mentioned,
    label_confidence,
    model_version,
    labelled_at,
    text_en
from {{ source('raw', 'labels') }}
qualify row_number() over (
    partition by post_id
    order by case when model_version like 'keyword%' then 1 else 0 end, labelled_at desc
) = 1
