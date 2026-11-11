with holidays as (
    select holiday_date
    from {{ ref('holidays') }}
    where country = '{{ var("mmm_ready_holiday_country") }}'
)

select
    o.date_day,
    o.geo,
    o.revenue,
    o.conversions,
    c.avg_price,
    coalesce(c.promo_flag, 0)                                as promo_flag,
    case when h.holiday_date is not null then 1 else 0 end   as holiday_flag
from {{ ref('stg_mmm__outcomes') }} o
left join {{ ref('stg_mmm__controls') }} c
    on o.date_day = c.date_day
   and o.geo = c.geo
left join holidays h
    on o.date_day = h.holiday_date
