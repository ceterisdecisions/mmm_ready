select
    cast({{ dbt.date_trunc('week', 'date_day') }} as date) as week_start,
    platform,
    mmm_channel,
    sum(spend) as spend
from {{ ref('int_mmm__spend_mapped') }}
group by 1, 2, 3
