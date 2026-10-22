-- One row per EV charging post. Cleaning only: types, dates, nulls. Incremental: each run adds new posts only.
{{ config(materialized='incremental', unique_key='post_id', incremental_strategy='delete+insert') }}

select
    post_id,
    created_at,
    cast(created_at as date)                       as created_date,
    cast(date_trunc('month', created_at) as date)  as created_month,
    text,
    lang,
    author_hash,
    follower_count,
    author_post_count,
    location_raw,
    region_tag,
    hashtags,
    is_retweet,
    is_reply,
    coalesce(like_count, 0)    as like_count,
    coalesce(retweet_count, 0) as retweet_count,
    coalesce(reply_count, 0)   as reply_count,
    coalesce(quote_count, 0)   as quote_count,
    coalesce(view_count, 0)    as view_count,
    search_themes,
    extracted_at,
    run_id,
    loaded_at
from {{ source('raw', 'posts') }}
where source_config = 'ev_charging'
{% if is_incremental() %}
  and loaded_at > (select max(loaded_at) from {{ this }})
{% endif %}
