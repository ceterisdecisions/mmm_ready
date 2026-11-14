select
    {{ mmm_ready.mmm_ready_week_start('date_day') }} as week_start,
    platform,
    mmm_channel,
    sum(spend) as spend
from {{ ref('int_mmm__spend_mapped') }}
group by 1, 2, 3
