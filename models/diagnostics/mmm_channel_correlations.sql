-- Weekly spend correlation (all geos combined) between channel pairs.
with weekly as (
    select week_start, mmm_channel, sum(spend) as spend
    from {{ ref('int_mmm__spend_weekly') }}
    where mmm_channel <> 'unmapped'
    group by 1, 2
)

select
    a.mmm_channel as channel_a,
    b.mmm_channel as channel_b,
    corr(a.spend, b.spend) as spend_correlation,
    case when abs(corr(a.spend, b.spend)) >= {{ var('mmm_ready_max_corr') }}
         then 1 else 0 end as is_collinear
from weekly a
join weekly b
    on a.week_start = b.week_start
   and a.mmm_channel < b.mmm_channel
group by 1, 2
