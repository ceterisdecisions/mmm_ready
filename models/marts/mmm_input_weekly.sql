-- depends_on: {{ ref('channel_taxonomy') }}
-- Wide week x geo table, ready for Meridian, PyMC-Marketing or Robyn.
-- One spend and one impressions column per MMM channel (dynamic pivot from the taxonomy).
{% set channels = mmm_ready.get_mmm_channels() %}

with spend as (
    select * from {{ ref('int_mmm__spend_weekly') }}
),

outcomes as (
    select
        cast({{ dbt.date_trunc('week', 'date_day') }} as date) as week_start,
        geo,
        sum(revenue)      as revenue,
        sum(conversions)  as conversions,
        avg(avg_price)    as avg_price,
        sum(promo_flag)   as promo_days,
        sum(holiday_flag) as holiday_days
    from {{ ref('fct_outcomes_daily') }}
    group by 1, 2
),

spend_pivot as (
    select
        week_start,
        geo,
        {%- for ch in channels %}
        sum(case when mmm_channel = '{{ ch }}' then spend else 0 end)       as spend_{{ ch }},
        sum(case when mmm_channel = '{{ ch }}' then impressions else 0 end) as impressions_{{ ch }},
        {%- endfor %}
        sum(case when mmm_channel = 'unmapped' then spend else 0 end)       as spend_unmapped
    from spend
    group by 1, 2
)

select
    o.week_start,
    o.geo,
    o.revenue,
    o.conversions,
    o.avg_price,
    o.promo_days,
    o.holiday_days,
    {%- for ch in channels %}
    coalesce(p.spend_{{ ch }}, 0)       as spend_{{ ch }},
    coalesce(p.impressions_{{ ch }}, 0) as impressions_{{ ch }},
    {%- endfor %}
    coalesce(p.spend_unmapped, 0)       as spend_unmapped
from outcomes o
left join spend_pivot p
    on o.week_start = p.week_start
   and o.geo = p.geo
