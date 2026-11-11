-- Maps each spend row to an MMM channel using the taxonomy.
-- Anything without a match becomes 'unmapped' (tracked by the diagnostics);
-- its demand_capture stays null because the channel's role is unknown.
select
    s.date_day,
    s.geo,
    s.platform,
    s.campaign_type,
    coalesce(t.mmm_channel, 'unmapped')   as mmm_channel,
    coalesce(t.channel_group, 'unmapped') as channel_group,
    t.demand_capture,
    s.spend,
    s.impressions,
    s.clicks
from {{ ref('stg_mmm__ad_spend') }} s
left join {{ ref('channel_taxonomy') }} t
    on s.platform = t.platform
   and s.campaign_type = t.campaign_type
