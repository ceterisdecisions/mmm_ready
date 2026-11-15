{{ config(enabled = (var('mmm_ready_outcomes_source', 'raw') | string | trim | lower) in ['ga4', 'firebase']
                   and mmm_ready.mmm_ready_as_bool(var('mmm_ready_ga4_dedupe_transactions', true))) }}
-- Reconciles int_mmm__ga4_outcomes_daily with the export it reads, written independently
-- of the model (group by + array_agg instead of qualify / row_number):
-- - conversions = distinct (event, dedupe key) + conversion events without one;
-- - revenue = for each dedupe key, the revenue of its earliest event, plus revenue of
--   events without one (key: transaction_id for ga4, user + timestamp for firebase);
-- - one row per (day, geo) with any event, and every day of the range present.
-- Settings (events, revenue field, geo level, countries) come from the same macros as the
-- model; the deduplication and aggregation are what is checked. Needs dedupe on (default).
-- Run against the public sample, see the README.
{%- set conversion_events = mmm_ready.mmm_ready_ga4_conversion_events() %}
{%- set countries = mmm_ready.mmm_ready_ga4_countries() %}
with export as (
    select
        parse_date('%Y%m%d', event_date) as date_day,
        event_name,
        event_timestamp,
        geo.{{ mmm_ready.mmm_ready_ga4_geo_field() }} as geo_value,
        {{ mmm_ready.mmm_ready_ga4_dedupe_key_expr() }} as dedupe_key,
        {{ mmm_ready.mmm_ready_ga4_revenue_expr() }} as revenue,
        event_name in ('{{ conversion_events | join("', '") }}') as is_conversion
    from {{ source('mmm_ready_ga4', 'events') }}
    where {{ mmm_ready.mmm_ready_ga4_table_suffix_filter() }}
    {%- if countries %}
      and geo.country in ('{{ countries | join("', '") }}')
    {%- endif %}
),

per_transaction as (
    select
        event_name,
        dedupe_key,
        array_agg(struct(revenue) order by event_timestamp limit 1)[offset(0)].revenue as revenue
    from export
    where is_conversion and dedupe_key is not null
    group by 1, 2
),

expected as (
    select
        (select count(*) from per_transaction)
          + (select count(*) from export where is_conversion and dedupe_key is null)
            as conversions,
        (select coalesce(sum(revenue), 0) from per_transaction)
          + (select coalesce(sum(revenue), 0) from export where is_conversion and dedupe_key is null)
            as revenue,
        (select count(distinct date_day) from export) as days,
        (select count(*) from (select distinct date_day, coalesce(geo_value, '(not set)') from export)) as panel_rows
),

actual as (
    select
        sum(conversions)        as conversions,
        sum(revenue)            as revenue,
        count(distinct date)    as days,
        count(*)                as panel_rows
    from {{ ref('int_mmm__ga4_outcomes_daily') }}
)

select e.*, a.conversions as actual_conversions, a.revenue as actual_revenue,
       a.days as actual_days, a.panel_rows as actual_panel_rows
from expected e
cross join actual a
where e.conversions <> a.conversions
   or abs(e.revenue - a.revenue) > 0.01
   or e.days <> a.days
   or e.panel_rows <> a.panel_rows
