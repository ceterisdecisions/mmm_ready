select
    cast(date as date)                     as date_day,
    lower(trim(geo))                       as geo,
    lower(trim(platform))                  as platform,
    lower(trim(campaign_type))             as campaign_type,
    cast(spend as {{ dbt.type_float() }})       as spend,
    cast(impressions as {{ dbt.type_bigint() }}) as impressions,
    cast(clicks as {{ dbt.type_bigint() }})      as clicks
from {{ source('mmm_ready_raw', 'ad_spend_daily') }}
