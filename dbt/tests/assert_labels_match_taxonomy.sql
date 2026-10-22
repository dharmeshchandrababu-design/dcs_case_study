-- Every theme and subcategory the labeller returned must exist in the taxonomy seed.
select l.post_id, l.theme, l.subcategory
from {{ ref('stg_labels') }} l
left join {{ ref('taxonomy') }} t on t.theme = l.theme and t.subcategory = l.subcategory
where t.theme is null
