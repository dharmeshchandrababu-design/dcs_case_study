-- One row per charging network, plus 'none' (no network named) and 'unknown' (a name not in the seed yet).
select lower(network) as network_key, network, network_role
from {{ ref('networks') }}
union all select 'none', 'No network named', 'none'
union all select 'unknown', 'Other network (not in seed)', 'unknown'
