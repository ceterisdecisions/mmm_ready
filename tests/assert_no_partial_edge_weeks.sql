-- With mmm_ready_drop_partial_weeks, the first and last week of mmm_input_weekly
-- must have 7 days of outcomes. Returns the offending weeks.
{% if mmm_ready.mmm_ready_as_bool(var('mmm_ready_drop_partial_weeks')) %}
with week_days as (
    select
        cast({{ dbt.date_trunc('week', 'date_day') }} as date) as week_start,
        count(distinct date_day)                               as n_days
    from {{ ref('fct_outcomes_daily') }}
    group by 1
),

edges as (
    select min(week_start) as first_week, max(week_start) as last_week
    from {{ ref('mmm_input_weekly') }}
)

select d.week_start, d.n_days
from week_days d
cross join edges e
where d.week_start in (e.first_week, e.last_week)
  and d.n_days < 7
{% else %}
select 1 as skipped where 1 = 0
{% endif %}
