{{ config(enabled = mmm_ready.mmm_ready_as_bool(var('mmm_ready_it_ai_fixture', false))) }}
-- Exact output of int_mmm__ga4_ai_referral_daily on the sample export written by
-- macros/create_ga4_ai_fixture.sql (run with mmm_ready_it_ai_fixture: true, see the README).
-- Cases, 2025-01-06:
--   chatgpt:    u1 referrer chatgpt.com (+ purchase T1 100), u3 no referrer but
--               utm_source=chatgpt.com (T3 50, sent twice: counted once), u8 chat.openai.com (T8 20)
--   perplexity: u2 www.perplexity.ai (subdomain)      gemini: u5 gemini.google.com
--   other:      u4 www.google.com (T4 30), u6 notchatgpt.com (not a subdomain), u7 direct
-- 2025-01-07:
--   claude: u9 claude.ai      copilot: u10 COPILOT.microsoft.com (case-insensitive)
--   other:  u1's second session lands from an internal page (T11 10); T12 5 has no session
with expected as (
    select * from unnest([
        struct(date '2025-01-06' as date, 'ai_referral' as traffic_source_group, 'chatgpt' as ai_assistant,
               3 as sessions, 3 as conversions, 170.0 as revenue),
        struct(date '2025-01-06', 'ai_referral', 'perplexity', 1, 0, 0.0),
        struct(date '2025-01-06', 'ai_referral', 'gemini',     1, 0, 0.0),
        struct(date '2025-01-06', 'other',       null,         3, 1, 30.0),
        struct(date '2025-01-07', 'ai_referral', 'claude',     1, 0, 0.0),
        struct(date '2025-01-07', 'ai_referral', 'copilot',    1, 0, 0.0),
        struct(date '2025-01-07', 'other',       null,         1, 2, 15.0)
    ])
),

actual as (
    select date, traffic_source_group, ai_assistant, sessions, conversions, revenue
    from {{ ref('int_mmm__ga4_ai_referral_daily') }}
    where geo = 'Madrid'
)

select 'missing' as problem, e.* from (select * from expected except distinct select * from actual) e
union all
select 'unexpected' as problem, a.* from (select * from actual except distinct select * from expected) a
union all
select 'other geo' as problem, date, traffic_source_group, ai_assistant, sessions, conversions, revenue
from {{ ref('int_mmm__ga4_ai_referral_daily') }}
where geo <> 'Madrid'
