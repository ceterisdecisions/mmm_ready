{#-
    Helpers for the optional GA4 / Firebase outcomes adapter (models/adapters/ga4/).

    BigQuery-only by necessity: the GA4 and Firebase exports exist only in BigQuery, as
    daily sharded tables events_YYYYMMDD queried through the events_* wildcard and
    _TABLE_SUFFIX. Everything here is inert unless mmm_ready_outcomes_source is set.

    dbt renders a model's SQL at parse time even when the model is disabled, so these
    checks only raise at execution (_mmm_ready_ga4_fail) and return harmless placeholders
    while parsing: a default build must not fail because the GA4 vars are unset.
-#}

{% macro _mmm_ready_ga4_fail(message) -%}
    {%- if execute -%}{{ exceptions.raise_compiler_error(message) }}{%- endif -%}
{%- endmacro %}

{#- Where outcomes_daily comes from: raw (default), ga4 (web) or firebase (app). -#}
{% macro mmm_ready_outcomes_source() -%}
    {%- set source = var('mmm_ready_outcomes_source', 'raw') | string | trim | lower -%}
    {%- if source not in ['raw', 'ga4', 'firebase'] -%}
        {{ mmm_ready._mmm_ready_ga4_fail(
            "mmm_ready_outcomes_source must be raw, ga4 or firebase, got '" ~ source ~ "'") }}
        {{ return('raw') }}
    {%- endif -%}
    {%- if source != 'raw' and target.type != 'bigquery' -%}
        {{ mmm_ready._mmm_ready_ga4_fail(
            "mmm_ready_outcomes_source = " ~ source ~ " needs a BigQuery target (the GA4/Firebase "
            ~ "export exists only in BigQuery); current target type is " ~ target.type) }}
    {%- endif -%}
    {{ return(source) }}
{%- endmacro %}


{#- A required YYYY-MM-DD var, returned as YYYYMMDD (the export's table suffix format). -#}
{% macro _mmm_ready_ga4_suffix(var_name) -%}
    {%- set value = var(var_name, none) -%}
    {%- if value is none or (value | string | trim) == '' -%}
        {{ mmm_ready._mmm_ready_ga4_fail(
            var_name ~ " is required when mmm_ready_outcomes_source is ga4 or firebase (YYYY-MM-DD). "
            ~ "It bounds the _TABLE_SUFFIX filter so the build never scans the whole export.") }}
        {{ return('00000000') }}
    {%- endif -%}
    {%- set text = value | string | trim -%}
    {%- set parsed = none -%}
    {#- Month/day ranges checked here: strptime errors cannot be caught in Jinja. -#}
    {%- if modules.re.match('^\\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\\d|3[01])$', text) -%}
        {%- set parsed = modules.datetime.datetime.strptime(text, '%Y-%m-%d') -%}
    {%- endif -%}
    {%- if parsed is none -%}
        {{ mmm_ready._mmm_ready_ga4_fail(var_name ~ " must be a YYYY-MM-DD date, got '" ~ text ~ "'") }}
        {{ return('00000000') }}
    {%- endif -%}
    {{ return(parsed.strftime('%Y%m%d')) }}
{%- endmacro %}


{#-
    Partition filter for the events_* wildcard. Always applied: without it BigQuery scans
    every daily table of the export. The YYYYMMDD bounds also exclude the
    events_intraday_* tables (their suffix starts with "intraday_").
-#}
{% macro mmm_ready_ga4_table_suffix_filter() -%}
    {%- set start = mmm_ready._mmm_ready_ga4_suffix('mmm_ready_ga4_start_date') -%}
    {%- set end = mmm_ready._mmm_ready_ga4_suffix('mmm_ready_ga4_end_date') -%}
    {%- if start > end -%}
        {{ mmm_ready._mmm_ready_ga4_fail(
            "mmm_ready_ga4_start_date must not be after mmm_ready_ga4_end_date") }}
    {%- endif -%}
    _TABLE_SUFFIX between '{{ start }}' and '{{ end }}'
{%- endmacro %}


{#-
    A list var of identifiers (event names, countries...) rendered as a SQL string list.
    Accepts a YAML list or a comma-separated string (values passed with --vars may arrive
    as either). Values are validated against `pattern` so they can be inlined safely.
-#}
{% macro _mmm_ready_string_list(var_name, default, pattern) -%}
    {%- set value = var(var_name, default) -%}
    {%- if value is none -%}{%- set value = [] -%}{%- endif -%}
    {%- if value is string -%}{%- set value = value.split(',') -%}{%- endif -%}
    {%- set items = [] -%}
    {%- for v in value -%}
        {%- set v = v | string | trim -%}
        {%- if v -%}
            {%- if modules.re.match(pattern, v) -%}
                {%- do items.append(v) -%}
            {%- else -%}
                {{ mmm_ready._mmm_ready_ga4_fail(var_name ~ ": invalid value '" ~ v ~ "'") }}
            {%- endif -%}
        {%- endif -%}
    {%- endfor -%}
    {{ return(items) }}
{%- endmacro %}


{#- Conversion event names; the default depends on the source (purchase for web). -#}
{% macro mmm_ready_ga4_conversion_events() -%}
    {%- set default = ['in_app_purchase'] if mmm_ready.mmm_ready_outcomes_source() == 'firebase' else ['purchase'] -%}
    {%- set events = mmm_ready._mmm_ready_string_list('mmm_ready_ga4_conversion_events', none, '^[A-Za-z0-9_]+$') -%}
    {{ return(events if events else default) }}
{%- endmacro %}


{#- Countries to keep (GA4 geo.country names, e.g. Spain). Empty = all countries. -#}
{% macro mmm_ready_ga4_countries() -%}
    {{ return(mmm_ready._mmm_ready_string_list('mmm_ready_ga4_countries', [], "^[A-Za-z0-9 .,'()&-]+$")) }}
{%- endmacro %}


{% macro mmm_ready_ga4_geo_field() -%}
    {%- set field = var('mmm_ready_ga4_geo_field', 'region') | string | trim | lower -%}
    {%- if field not in ['country', 'region', 'city'] -%}
        {{ mmm_ready._mmm_ready_ga4_fail(
            "mmm_ready_ga4_geo_field must be country, region or city, got '" ~ field ~ "'") }}
        {{ return('region') }}
    {%- endif -%}
    {{ return(field) }}
{%- endmacro %}


{#-
    Revenue column of the export to sum, as a SQL expression. Default by source:
    ecommerce.purchase_revenue for ga4; event_value_in_usd for firebase, whose export
    (at least the 2018 schema of the public sample) has no ecommerce struct.
-#}
{% macro mmm_ready_ga4_revenue_expr() -%}
    {%- set source = mmm_ready.mmm_ready_outcomes_source() -%}
    {%- set exprs = {
        'purchase_revenue': 'ecommerce.purchase_revenue',
        'purchase_revenue_in_usd': 'ecommerce.purchase_revenue_in_usd',
        'event_value_in_usd': 'event_value_in_usd',
    } -%}
    {%- set allowed = ['event_value_in_usd'] if source == 'firebase' else exprs.keys() | list -%}
    {%- set default = 'event_value_in_usd' if source == 'firebase' else 'purchase_revenue' -%}
    {%- set field = var('mmm_ready_ga4_revenue_field', none) -%}
    {%- set field = default if field is none or (field | string | trim) == '' else field | string | trim | lower -%}
    {%- if field not in allowed -%}
        {{ mmm_ready._mmm_ready_ga4_fail(
            "mmm_ready_ga4_revenue_field must be one of " ~ allowed | join(', ') ~ " with mmm_ready_outcomes_source = "
            ~ source ~ ", got '" ~ field ~ "'") }}
        {{ return(exprs[default]) }}
    {%- endif -%}
    {{ return(exprs[field]) }}
{%- endmacro %}


{#-
    Key that identifies repeated conversions, as a SQL expression; null = never deduplicated.
    ga4: the transaction_id ('(not set)' treated as missing), as GA4 reports do.
    firebase: no transaction_id in the export, so only exact repeats of the same event
    (same user and timestamp) are dropped.
-#}
{% macro mmm_ready_ga4_dedupe_key_expr() -%}
    {%- if mmm_ready.mmm_ready_outcomes_source() == 'firebase' -%}
        {{ return("concat(user_pseudo_id, '|', cast(event_timestamp as string))") }}
    {%- endif -%}
    {{ return("nullif(ecommerce.transaction_id, '(not set)')") }}
{%- endmacro %}


{#-
    Checks for int_mmm__ga4_ai_referral_daily: BigQuery only, and web only (an app export has
    no referrer, so the model is rejected with mmm_ready_outcomes_source = firebase).
-#}
{% macro mmm_ready_ga4_ai_referral_checks() -%}
    {%- if target.type != 'bigquery' -%}
        {{ mmm_ready._mmm_ready_ga4_fail(
            "mmm_ready_ga4_ai_referral needs a BigQuery target (the GA4 export exists only in "
            ~ "BigQuery); current target type is " ~ target.type) }}
    {%- endif -%}
    {%- if mmm_ready.mmm_ready_outcomes_source() == 'firebase' -%}
        {{ mmm_ready._mmm_ready_ga4_fail(
            "mmm_ready_ga4_ai_referral is for GA4 web exports: Firebase app events have no referrer") }}
    {%- endif -%}
{%- endmacro %}


{#- Value of an event_params key (string_value or int_value), as a SQL expression. -#}
{% macro mmm_ready_ga4_param(key, kind='string') -%}
    (select value.{{ kind }}_value from unnest(event_params) where key = '{{ key }}' limit 1)
{%- endmacro %}


{#-
    True when host_expr is domain_expr or a subdomain of it. "notchatgpt.com" does not
    match chatgpt.com: a subdomain needs the dot.
-#}
{% macro mmm_ready_ga4_domain_match(host_expr, domain_expr) -%}
    ({{ host_expr }} = {{ domain_expr }} or ends_with({{ host_expr }}, concat('.', {{ domain_expr }})))
{%- endmacro %}
