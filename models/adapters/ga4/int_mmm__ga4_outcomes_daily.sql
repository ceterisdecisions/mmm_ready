{{ config(enabled = (var('mmm_ready_outcomes_source', 'raw') | string | trim | lower) in ['ga4', 'firebase']) }}
-- outcomes_daily built from the GA4 BigQuery export, web (ga4) or app (firebase: the same
-- export format for Firebase apps), with the raw table's columns: date, geo, revenue,
-- conversions. Enabled with mmm_ready_outcomes_source: ga4 | firebase.
--
-- BigQuery-only by necessity (see macros/ga4_adapter.sql). Reads only the columns below,
-- never event_params (~10x the bytes on the public sample).
-- - date:        event_date (the property's reporting time zone), parsed from YYYYMMDD.
-- - geo:         geo.<mmm_ready_ga4_geo_field>, '(not set)' when missing.
-- - conversions: events named in mmm_ready_ga4_conversion_events
--                (default purchase / in_app_purchase).
-- - revenue:     mmm_ready_ga4_revenue_field of those events
--                (default ecommerce.purchase_revenue / event_value_in_usd).
-- With mmm_ready_ga4_dedupe_transactions (default true) each dedupe key counts once, keeping
-- its earliest event: the transaction_id for ga4, as GA4 reports do (events without one are
-- all kept); user + timestamp for firebase, which has no transaction_id (exact repeats only).
-- Every (day, geo) with any event is in the panel, so a day without conversions is 0, not missing.
{%- do mmm_ready.mmm_ready_outcomes_source() %}  {#- validates the var and that the target is BigQuery -#}
{%- set conversion_events = mmm_ready.mmm_ready_ga4_conversion_events() %}
{%- set countries = mmm_ready.mmm_ready_ga4_countries() %}
{%- set dedupe = mmm_ready.mmm_ready_as_bool(var('mmm_ready_ga4_dedupe_transactions', true)) %}

with events as (
    select
        parse_date('%Y%m%d', event_date)                                  as date_day,
        coalesce(geo.{{ mmm_ready.mmm_ready_ga4_geo_field() }}, '(not set)') as geo,
        event_name,
        event_timestamp,
        {{ mmm_ready.mmm_ready_ga4_dedupe_key_expr() }}                    as dedupe_key,
        {{ mmm_ready.mmm_ready_ga4_revenue_expr() }}                       as revenue
    from {{ source('mmm_ready_ga4', 'events') }}
    where {{ mmm_ready.mmm_ready_ga4_table_suffix_filter() }}
    {%- if countries %}
      and geo.country in ('{{ countries | join("', '") }}')
    {%- endif %}
),

conversions as (
    select date_day, geo, revenue
    from events
    where event_name in ('{{ conversion_events | join("', '") }}')
    {%- if dedupe %}
    qualify dedupe_key is null
         or row_number() over (
                partition by event_name, dedupe_key
                order by event_timestamp, date_day, geo
            ) = 1
    {%- endif %}
),

panel as (
    select distinct date_day, geo from events
),

daily as (
    select date_day, geo, sum(coalesce(revenue, 0)) as revenue, count(*) as conversions
    from conversions
    group by 1, 2
)

select
    p.date_day                    as date,
    p.geo,
    coalesce(d.revenue, 0)        as revenue,
    coalesce(d.conversions, 0)    as conversions
from panel p
left join daily d
    on p.date_day = d.date_day
   and p.geo = d.geo
