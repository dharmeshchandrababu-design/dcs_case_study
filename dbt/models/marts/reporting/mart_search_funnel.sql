-- Data quality view: how much of what each search returns is really about EV charging?
-- One row per search (theme and language). A post found by several searches counts in each of them.
with labelled as (
    select
        p.post_id,
        p.lang,
        p.region_tag,
        trim(s.search)                     as search,
        l.relevant
    from {{ ref('stg_posts') }} p
    join {{ ref('stg_labels') }} l using (post_id)
    cross join unnest(string_split(p.search_themes, ';')) as s(search)
)
select
    search,
    -- '1 Access/roaming|de' -> topic 'Access/roaming', language 'de', so searches can be grouped either way
    trim(regexp_replace(split_part(search, '|', 1), '^[0-9]+ ', ''))   as search_topic,
    split_part(search, '|', 2)                                          as search_lang,
    count(*)                                                     as posts_found,
    sum(case when relevant then 1 else 0 end)                    as relevant_posts,
    round(avg(case when relevant then 1 else 0 end), 2)          as relevance_rate,
    sum(case when relevant and region_tag <> 'outside' then 1 else 0 end) as relevant_in_scope
from labelled
where search <> ''
group by all
order by relevance_rate desc, posts_found desc
