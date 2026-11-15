{{ config(enabled = mmm_ready.mmm_ready_as_bool(var('mmm_ready_ga4_ai_referral', false))) }}
-- GA4 web traffic from AI assistants, as its own source: sessions, conversions and revenue
-- per day, geo and traffic_source_group ('ai_referral' | 'other'). Not an MMM channel (it is
-- unpaid); a separate table to track alongside mmm_input_weekly. Enabled with
-- mmm_ready_ga4_ai_referral: true; reads the same export, dates and countries as the outcomes
-- adapter (mmm_ready_ga4_* vars), whatever mmm_ready_outcomes_source is.
--
-- BigQuery and GA4 web only (see macros/ga4_adapter.sql). Reads event_params, which the
-- outcomes adapter avoids: about 7x its bytes on the public sample.
-- Session = user_pseudo_id + ga_session_id, classified by its landing page (first event with
-- a page_location; later pages carry internal referrers). It is ai_referral when the landing
-- page's referrer host, or else its utm_source (ChatGPT adds utm_source=chatgpt.com), is a
-- domain of the ai_referral_domains seed or a subdomain of one (longest match wins).
-- Only event_params are used: they exist in every export version, unlike
-- collected_traffic_source / session_traffic_source_last_click (2023+).
-- Sessions count on the day and geo of their first event; conversions on the day and geo of
-- the conversion event, deduplicated like int_mmm__ga4_outcomes_daily, so conversion and
-- revenue totals match that model. Conversions outside any session go to 'other'.
{%- do mmm_ready.mmm_ready_ga4_ai_referral_checks() %}
{%- set conversion_events = mmm_ready.mmm_ready_ga4_conversion_events() %}
{%- set countries = mmm_ready.mmm_ready_ga4_countries() %}
{%- set dedupe = mmm_ready.mmm_ready_as_bool(var('mmm_ready_ga4_dedupe_transactions', true)) %}

with events as (
    select
        parse_date('%Y%m%d', event_date)                                  as date_day,
        coalesce(geo.{{ mmm_ready.mmm_ready_ga4_geo_field() }}, '(not set)') as geo,
        event_name,
        event_timestamp,
        user_pseudo_id,
        {{ mmm_ready.mmm_ready_ga4_param('ga_session_id', 'int') }}         as ga_session_id,
        {{ mmm_ready.mmm_ready_ga4_param('page_referrer') }}               as page_referrer,
        {{ mmm_ready.mmm_ready_ga4_param('page_location') }}               as page_location,
        {{ mmm_ready.mmm_ready_ga4_dedupe_key_expr() }}                    as dedupe_key,
        {{ mmm_ready.mmm_ready_ga4_revenue_expr() }}                       as revenue
    from {{ source('mmm_ready_ga4', 'events') }}
    where {{ mmm_ready.mmm_ready_ga4_table_suffix_filter() }}
    {%- if countries %}
      and geo.country in ('{{ countries | join("', '") }}')
    {%- endif %}
),

sessions as (
    select
        user_pseudo_id,
        ga_session_id,
        array_agg(struct(date_day, geo) order by event_timestamp limit 1)[offset(0)] as first_event,
        array_agg(
            if(page_location is null, null, struct(
                lower(net.host(page_referrer)) as referrer_host,
                lower(regexp_extract(page_location, r'[?&]utm_source=([^&#]+)')) as utm_source))
            ignore nulls order by event_timestamp limit 1
        )[safe_offset(0)] as landing
    from events
    where ga_session_id is not null
    group by 1, 2
),

-- Best seed match per session: referrer before utm_source, then the longest domain.
-- (A cross join with the small seed: BigQuery rejects correlated subqueries on other tables.)
{%- set ref_match = mmm_ready.mmm_ready_ga4_domain_match('s.landing.referrer_host', 'd.domain') %}
{%- set utm_match = mmm_ready.mmm_ready_ga4_domain_match('s.landing.utm_source', 'd.domain') %}
matches as (
    select s.user_pseudo_id, s.ga_session_id, d.assistant
    from sessions s
    cross join {{ ref('ai_referral_domains') }} d
    where {{ ref_match }} or {{ utm_match }}
    qualify row_number() over (
        partition by s.user_pseudo_id, s.ga_session_id
        order by case when {{ ref_match }} then 0 else 1 end, length(d.domain) desc
    ) = 1
),

classified as (
    select
        s.user_pseudo_id,
        s.ga_session_id,
        s.first_event.date_day as date_day,
        s.first_event.geo      as geo,
        m.assistant            as ai_assistant
    from sessions s
    left join matches m
        on s.user_pseudo_id = m.user_pseudo_id
       and s.ga_session_id = m.ga_session_id
),

conversions as (
    select date_day, geo, user_pseudo_id, ga_session_id, revenue
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

session_counts as (
    select date_day, geo, ai_assistant, count(*) as sessions, 0 as conversions, 0.0 as revenue
    from classified
    group by 1, 2, 3
),

conversion_counts as (
    select
        c.date_day,
        c.geo,
        k.ai_assistant,
        0                             as sessions,
        count(*)                      as conversions,
        sum(coalesce(c.revenue, 0))   as revenue
    from conversions c
    left join classified k
        on c.user_pseudo_id = k.user_pseudo_id
       and c.ga_session_id = k.ga_session_id
    group by 1, 2, 3
)

select
    date_day                                                         as date,
    geo,
    case when ai_assistant is not null then 'ai_referral' else 'other' end as traffic_source_group,
    ai_assistant,
    sum(sessions)                                                    as sessions,
    sum(conversions)                                                 as conversions,
    sum(revenue)                                                     as revenue
from (
    select * from session_counts
    union all
    select * from conversion_counts
) x
group by 1, 2, 3, 4
