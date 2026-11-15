{#-
    Sample GA4 export for testing int_mmm__ga4_ai_referral_daily: the public sample (2020-21)
    has no AI-assistant traffic. Creates <target.schema>_ga4_fixture.events_20250106 and
    events_20250107 with the subset of the GA4 export schema the adapter reads.

        dbt run-operation create_ga4_ai_fixture --target bigquery

    Each session is one user (u1..u12) unless noted; all in Spain / Madrid. Expected result:
    tests/assert_ga4_ai_referral_fixture.sql.
-#}
{% macro create_ga4_ai_fixture() %}
    {%- if target.type != 'bigquery' -%}
        {{ exceptions.raise_compiler_error("create_ga4_ai_fixture needs the bigquery target") }}
    {%- endif -%}
    {%- set dataset = target.schema ~ '_ga4_fixture' -%}
    {#- day, user, session, seconds, event, referrer, location, transaction_id, revenue -#}
    {%- set shop = 'https://shop.example/' -%}
    {%- set rows = [
        ('20250106', 'u1', 1, 0, 'session_start', 'https://chatgpt.com/', shop, none, none),
        ('20250106', 'u1', 1, 1, 'page_view', 'https://chatgpt.com/', shop, none, none),
        ('20250106', 'u1', 1, 9, 'page_view', shop, shop ~ 'cart', none, none),
        ('20250106', 'u1', 1, 20, 'purchase', shop ~ 'cart', shop ~ 'thanks', 'T1', 100.0),
        ('20250106', 'u2', 1, 0, 'page_view', 'https://www.perplexity.ai/search?q=shoes', shop, none, none),
        ('20250106', 'u3', 1, 0, 'page_view', none, shop ~ '?utm_source=chatgpt.com&utm_medium=referral', none, none),
        ('20250106', 'u3', 1, 5, 'page_view', shop, shop ~ 'cart', none, none),
        ('20250106', 'u3', 1, 30, 'purchase', shop ~ 'cart', shop ~ 'thanks', 'T3', 50.0),
        ('20250106', 'u3', 1, 31, 'purchase', shop ~ 'cart', shop ~ 'thanks', 'T3', 50.0),
        ('20250106', 'u4', 1, 0, 'page_view', 'https://www.google.com/', shop, none, none),
        ('20250106', 'u4', 1, 40, 'purchase', shop ~ 'cart', shop ~ 'thanks', 'T4', 30.0),
        ('20250106', 'u5', 1, 0, 'page_view', 'https://gemini.google.com/app', shop, none, none),
        ('20250106', 'u6', 1, 0, 'page_view', 'https://notchatgpt.com/', shop, none, none),
        ('20250106', 'u7', 1, 0, 'page_view', none, shop, none, none),
        ('20250106', 'u8', 1, 0, 'page_view', 'https://chat.openai.com/c/abc', shop, none, none),
        ('20250106', 'u8', 1, 50, 'purchase', shop ~ 'cart', shop ~ 'thanks', 'T8', 20.0),
        ('20250107', 'u9', 1, 0, 'page_view', 'https://claude.ai/chat/1', shop, none, none),
        ('20250107', 'u10', 1, 0, 'page_view', 'https://COPILOT.microsoft.com/', shop, none, none),
        ('20250107', 'u1', 2, 0, 'page_view', shop, shop ~ 'cart', none, none),
        ('20250107', 'u1', 2, 10, 'purchase', shop ~ 'cart', shop ~ 'thanks', 'T11', 10.0),
        ('20250107', 'u12', none, 0, 'purchase', none, shop ~ 'thanks', 'T12', 5.0),
    ] -%}
    {%- for day in ['20250106', '20250107'] %}
        {%- set selects = [] -%}
        {%- for r in rows if r[0] == day -%}
            {%- set params = [] -%}
            {%- if r[2] is not none -%}
                {%- do params.append("struct('ga_session_id' as key, struct(cast(null as string) as string_value, " ~ r[2] ~ " as int_value, cast(null as float64) as float_value, cast(null as float64) as double_value) as value)") -%}
            {%- endif -%}
            {%- for key, val in [('page_referrer', r[5]), ('page_location', r[6])] if val is not none -%}
                {%- do params.append("struct('" ~ key ~ "' as key, struct('" ~ val ~ "' as string_value, cast(null as int64) as int_value, cast(null as float64) as float_value, cast(null as float64) as double_value) as value)") -%}
            {%- endfor -%}
            {%- set ts = "unix_micros(timestamp('" ~ day[:4] ~ "-" ~ day[4:6] ~ "-" ~ day[6:] ~ " 10:00:00')) + " ~ (r[1][1:] | int) * 1000000000 ~ " + " ~ r[3] * 1000000 -%}
            {%- do selects.append(
                "select '" ~ day ~ "' as event_date, " ~ ts ~ " as event_timestamp, '" ~ r[4] ~ "' as event_name, '"
                ~ r[1] ~ "' as user_pseudo_id, "
                ~ "array<struct<key string, value struct<string_value string, int_value int64, float_value float64, double_value float64>>>["
                ~ params | join(', ') ~ "] as event_params, "
                ~ "struct('Spain' as country, 'Madrid' as region, 'Madrid' as city) as geo, "
                ~ "struct(" ~ (r[8] if r[8] is not none else 'cast(null as float64)') ~ " as purchase_revenue, "
                ~ (r[8] if r[8] is not none else 'cast(null as float64)') ~ " as purchase_revenue_in_usd, "
                ~ ("'" ~ r[7] ~ "'" if r[7] is not none else 'cast(null as string)') ~ " as transaction_id) as ecommerce, "
                ~ (r[8] if r[8] is not none else 'cast(null as float64)') ~ " as event_value_in_usd"
            ) -%}
        {%- endfor -%}
        {%- set sql -%}
            create schema if not exists `{{ target.database }}`.`{{ dataset }}` options (location = '{{ target.location }}');
            create or replace table `{{ target.database }}`.`{{ dataset }}`.`events_{{ day }}` as
            {{ selects | join('\nunion all\n') }}
        {%- endset -%}
        {%- do run_query(sql) -%}
        {{ log("created " ~ dataset ~ ".events_" ~ day ~ " (" ~ selects | length ~ " events)", info=true) }}
    {%- endfor %}
{% endmacro %}
