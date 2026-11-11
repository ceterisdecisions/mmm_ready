-- Asigna cada fila de inversión a un canal MMM según la taxonomía.
-- Lo que no encuentra mapeo queda como 'unmapped' (lo vigila el diagnóstico).
select
    s.date_day,
    s.geo,
    s.platform,
    s.campaign_type,
    coalesce(t.mmm_channel, 'unmapped')   as mmm_channel,
    coalesce(t.channel_group, 'unmapped') as channel_group,
    s.spend,
    s.impressions,
    s.clicks
from {{ ref('stg_mmm__ad_spend') }} s
left join {{ ref('channel_taxonomy') }} t
    on s.platform = t.platform
   and s.campaign_type = t.campaign_type
