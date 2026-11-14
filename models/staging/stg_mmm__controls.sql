select
    cast(date as date)                    as date_day,
    lower(trim(geo))                      as geo,
    cast(avg_price as {{ mmm_ready.mmm_ready_type_double() }}) as avg_price,
    cast(promo_flag as {{ dbt.type_int() }})  as promo_flag
from {{ source('mmm_ready_raw', 'controls_daily') }}
