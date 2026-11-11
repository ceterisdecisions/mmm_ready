select
    date_day,
    geo,
    mmm_channel,
    channel_group,
    demand_capture,
    sum(spend)       as spend,
    sum(impressions) as impressions,
    sum(clicks)      as clicks
from {{ ref('int_mmm__spend_mapped') }}
group by 1, 2, 3, 4, 5
