{{ config(enabled = (var('mmm_ready_outcomes_source', 'raw') | string | trim | lower) == 'ga4') }}
-- Reconciles int_mmm__ga4_outcomes_daily with the export it reads, written independently
-- of the model (group by + array_agg instead of qualify / row_number):
-- - conversions = distinct transaction_ids + purchase events without one;
-- - revenue = for each transaction_id, the revenue of its earliest event, plus revenue of
--   events without one;
-- - one row per (day, geo) with any event, and every day of the range present.
-- Only for the default adapter settings (purchase, purchase_revenue, region, deduped).
-- Run against the public sample, see the README.
with export as (
    select
        parse_date('%Y%m%d', event_date) as date_day,
        event_name,
        event_timestamp,
        geo.region as region,
        nullif(ecommerce.transaction_id, '(not set)') as transaction_id,
        ecommerce.purchase_revenue as revenue
    from {{ source('mmm_ready_ga4', 'events') }}
    where {{ mmm_ready.mmm_ready_ga4_table_suffix_filter() }}
),

per_transaction as (
    select
        transaction_id,
        array_agg(struct(revenue) order by event_timestamp limit 1)[offset(0)].revenue as revenue
    from export
    where event_name = 'purchase' and transaction_id is not null
    group by 1
),

expected as (
    select
        (select count(*) from per_transaction)
          + (select count(*) from export where event_name = 'purchase' and transaction_id is null)
            as conversions,
        (select coalesce(sum(revenue), 0) from per_transaction)
          + (select coalesce(sum(revenue), 0) from export where event_name = 'purchase' and transaction_id is null)
            as revenue,
        (select count(distinct date_day) from export) as days,
        (select count(*) from (select distinct date_day, coalesce(region, '(not set)') from export)) as panel_rows
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
