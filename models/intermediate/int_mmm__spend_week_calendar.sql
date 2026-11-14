-- One row per week of spend data, with the number of distinct days that have spend rows
-- (all geos and platforms combined). The first and last week are partial when they have
-- fewer than 7 days: the diagnostics leave them out of per-week statistics (variance,
-- spikes, sparsity, correlations), because a near-empty edge week looks like real variation.
with week_days as (
    select
        cast({{ dbt.date_trunc('week', 'date_day') }} as date) as week_start,
        count(distinct date_day)                               as n_days
    from {{ ref('int_mmm__spend_mapped') }}
    group by 1
),

bounds as (
    select min(week_start) as first_week, max(week_start) as last_week
    from week_days
)

select
    w.week_start,
    w.n_days,
    case when w.week_start in (b.first_week, b.last_week) and w.n_days < 7
         then 1 else 0 end as is_partial_edge_week
from week_days w
cross join bounds b
