-- The problem ranking must have exactly one row per theme and subcategory.
select theme, subcategory, count(*) as rows
from {{ ref('mart_problem_ranking') }}
group by theme, subcategory
having count(*) > 1
