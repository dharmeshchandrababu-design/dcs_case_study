-- Share of voice across all networks must add up to 1 (small rounding allowed), unless no post names a network.
select sum(share_of_voice) as total
from {{ ref('mart_network_scorecard') }}
having sum(posts) > 0 and abs(sum(share_of_voice) - 1) > 0.02
