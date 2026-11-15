{{ config(enabled = (var('mmm_ready_outcomes_source', 'raw') | string | trim | lower) == 'ga4'
                   and mmm_ready.mmm_ready_as_bool(var('mmm_ready_ga4_ai_referral', false))) }}
-- int_mmm__ga4_ai_referral_daily splits the same conversions as int_mmm__ga4_outcomes_daily
-- by traffic source, so their per-day totals must match.
with ai as (
    select date, sum(conversions) as conversions, sum(revenue) as revenue
    from {{ ref('int_mmm__ga4_ai_referral_daily') }}
    group by 1
),

outcomes as (
    select date, sum(conversions) as conversions, sum(revenue) as revenue
    from {{ ref('int_mmm__ga4_outcomes_daily') }}
    group by 1
)

select coalesce(a.date, o.date) as date, a.conversions as ai_conversions, o.conversions,
       a.revenue as ai_revenue, o.revenue
from ai a
full outer join outcomes o on a.date = o.date
where coalesce(a.conversions, 0) <> coalesce(o.conversions, 0)
   or abs(coalesce(a.revenue, 0) - coalesce(o.revenue, 0)) > 0.01
