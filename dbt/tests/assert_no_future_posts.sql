-- No post can be written after it was fetched.
select post_id, created_at, extracted_at
from {{ ref('stg_posts') }}
where created_at > coalesce(extracted_at, loaded_at)
