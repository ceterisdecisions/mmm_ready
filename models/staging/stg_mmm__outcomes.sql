select
    cast(date as date)                      as date_day,
    lower(trim(geo))                        as geo,
    cast(revenue as {{ dbt.type_float() }})      as revenue,
    cast(conversions as {{ dbt.type_bigint() }}) as conversions
from {{ source('mmm_ready_raw', 'outcomes_daily') }}
