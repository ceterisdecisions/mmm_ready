-- Weekly spend correlation (all geos combined) between channel pairs.
-- Partial edge weeks are skipped: near-empty weeks at both ends make every pair look correlated.
with weekly as (
    select w.week_start, w.mmm_channel, sum(w.spend) as spend
    from {{ ref('int_mmm__spend_weekly') }} w
    join {{ ref('int_mmm__spend_week_calendar') }} cal
        on w.week_start = cal.week_start
    where w.mmm_channel <> 'unmapped'
      and cal.is_partial_edge_week = 0
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
