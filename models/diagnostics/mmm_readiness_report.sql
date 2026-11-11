-- Per-channel diagnostic: is this data fit for an MMM?
-- blocked: unmapped spend, short history or weeks without KPI.
-- review:  platform gaps (data loss or flighting: confirm which),
--          sparse channel, low variance, spikes or collinearity.
with weekly as (
    select week_start, mmm_channel, sum(spend) as spend
    from {{ ref('int_mmm__spend_weekly') }}
    group by 1, 2
),

kpi_weeks as (
    select distinct cast({{ dbt.date_trunc('week', 'date_day') }} as date) as week_start
    from {{ ref('fct_outcomes_daily') }}
    where revenue is not null
),

calendar as (
    select
        min(week_start) as first_week,
        max(week_start) as last_week,
        {{ dbt.datediff('min(week_start)', 'max(week_start)', 'week') }} + 1 as expected_weeks
    from weekly
),

kpi_coverage as (
    select count(*) as kpi_weeks
    from kpi_weeks k
    cross join calendar c
    where k.week_start between c.first_week and c.last_week
),

totals as (
    select sum(spend) as total_spend from weekly
),

-- Per-platform gaps: weeks with no rows between its first and last active week
platform_gaps as (
    select
        mmm_channel,
        platform,
        ({{ dbt.datediff('min(week_start)', 'max(week_start)', 'week') }} + 1)
            - count(distinct week_start) as gap_weeks
    from {{ ref('int_mmm__platform_weekly') }}
    group by 1, 2
),

channel_gaps as (
    select
        mmm_channel,
        sum(gap_weeks) as gap_weeks,
        {{ dbt.listagg("case when gap_weeks > 0 then platform end", "', '") }} as platforms_with_gaps
    from platform_gaps
    group by 1
),

channel_stats as (
    select
        mmm_channel,
        sum(case when spend > 0 then 1 else 0 end) as weeks_with_spend,
        sum(spend)                                 as total_spend,
        avg(spend)                                 as avg_weekly_spend,
        stddev_samp(spend)                         as std_weekly_spend,
        max(spend)                                 as max_weekly_spend
    from weekly
    group by 1
),

collinear as (
    select channel, max(is_collinear) as is_collinear, max(abs(spend_correlation)) as max_abs_corr
    from (
        select channel_a as channel, is_collinear, spend_correlation from {{ ref('mmm_channel_correlations') }}
        union all
        select channel_b as channel, is_collinear, spend_correlation from {{ ref('mmm_channel_correlations') }}
    ) x
    group by 1
),

checks as (
    select
        s.mmm_channel,
        c.expected_weeks,
        kc.kpi_weeks,
        s.weeks_with_spend,
        g.gap_weeks,
        g.platforms_with_gaps,
        s.total_spend,
        s.total_spend / nullif(t.total_spend, 0)                       as spend_share,
        1 - (s.weeks_with_spend * 1.0 / nullif(c.expected_weeks, 0))   as zero_week_share,
        s.std_weekly_spend / nullif(s.avg_weekly_spend, 0)             as cv_weekly_spend,
        s.max_weekly_spend / nullif(s.avg_weekly_spend, 0)             as spike_ratio,
        coalesce(k.max_abs_corr, 0)                                    as max_abs_corr_with_other,

        -- blocking
        case when s.mmm_channel = 'unmapped'
              and s.total_spend / nullif(t.total_spend, 0) > {{ var('mmm_ready_max_unmapped_share') }}
             then 1 else 0 end                                         as flag_unmapped_spend,
        case when c.expected_weeks < {{ var('mmm_ready_min_history_weeks') }}
             then 1 else 0 end                                         as flag_short_history,
        case when kc.kpi_weeks < c.expected_weeks
             then 1 else 0 end                                         as flag_kpi_gaps,
        -- review
        case when g.gap_weeks > 0 then 1 else 0 end                    as flag_platform_gaps,
        case when 1 - (s.weeks_with_spend * 1.0 / nullif(c.expected_weeks, 0)) > {{ var('mmm_ready_max_zero_week_share') }}
             then 1 else 0 end                                         as flag_sparse,
        case when s.std_weekly_spend / nullif(s.avg_weekly_spend, 0) < {{ var('mmm_ready_min_cv') }}
             then 1 else 0 end                                         as flag_low_variance,
        case when s.max_weekly_spend / nullif(s.avg_weekly_spend, 0) > {{ var('mmm_ready_max_spike_ratio') }}
             then 1 else 0 end                                         as flag_spike,
        coalesce(k.is_collinear, 0)                                    as flag_collinear
    from channel_stats s
    cross join calendar c
    cross join kpi_coverage kc
    cross join totals t
    left join channel_gaps g on s.mmm_channel = g.mmm_channel
    left join collinear k   on s.mmm_channel = k.channel
)

select
    *,
    flag_unmapped_spend + flag_short_history + flag_kpi_gaps            as blocking_issues,
    flag_platform_gaps + flag_sparse + flag_low_variance
      + flag_spike + flag_collinear                                     as review_issues,
    case
        when flag_unmapped_spend + flag_short_history + flag_kpi_gaps > 0 then 'blocked'
        when flag_platform_gaps + flag_sparse + flag_low_variance
           + flag_spike + flag_collinear > 0 then 'review'
        else 'ready'
    end                                                                 as readiness_status
from checks
