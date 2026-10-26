-- One label per post. Incremental: each run only looks at posts whose labels changed since the last run
-- (a new label or a new translation), re-ranks all labels of those posts, and replaces their row by post_id.
-- Ranking: AI labels win over the keyword baseline, the newest label wins within the same labeller.
{{ config(materialized='incremental', unique_key='post_id', incremental_strategy='delete+insert') }}

with labels as (
    select *, coalesce(updated_at, labelled_at) as changed_at
    from {{ source('raw', 'labels') }}
),

changed_posts as (
    select distinct post_id
    from labels
    {% if is_incremental() %}
    where changed_at > (select max(changed_at) from {{ this }})
    {% endif %}
)

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
    text_en,
    changed_at
from labels
where post_id in (select post_id from changed_posts)
qualify row_number() over (
    partition by post_id
    order by case when model_version like 'keyword%' then 1 else 0 end, labelled_at desc
) = 1
