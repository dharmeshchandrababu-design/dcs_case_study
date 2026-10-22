-- One row per theme and subcategory, with the business view: who owns it and can DCS fix it.
select
    theme || '/' || subcategory as theme_key,
    theme,
    subcategory,
    owner_team,
    dcs_lever,
    case dcs_lever when 'yes' then 1.0 when 'partly' then 0.6 else 0.2 end as lever_weight,
    description
from {{ ref('taxonomy') }}
