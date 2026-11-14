select
    cast(date as date)                      as date_day,
    lower(trim(geo))                        as geo,
    cast(revenue as {{ mmm_ready.mmm_ready_type_double() }})      as revenue,
    cast(conversions as {{ dbt.type_bigint() }}) as conversions
from {{ source('mmm_ready_raw', 'outcomes_daily') }}
