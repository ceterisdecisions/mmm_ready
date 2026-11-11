select
    cast(date as date)                    as date_day,
    lower(trim(geo))                      as geo,
    cast(avg_price as {{ dbt.type_float() }}) as avg_price,
    cast(promo_flag as {{ dbt.type_int() }})  as promo_flag
from {{ source('mmm_ready_raw', 'controls_daily') }}
