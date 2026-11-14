{#-
    Start of the week containing date_expr, as a date. The weekday comes from the
    mmm_ready_week_start var (monday by default), so every warehouse returns the same
    weeks: dbt.date_trunc('week', ...) follows each warehouse's convention instead
    (DuckDB: Monday, BigQuery: Sunday).

    Computed as date - mod(days since an anchor, 7). The anchor is the first chosen
    weekday on or after 1900-01-01 (a Monday), so the day difference is never negative
    for real data and mod() behaves the same everywhere.
-#}
{% macro mmm_ready_week_start(date_expr) -%}
    {%- set weekdays = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'] -%}
    {%- set day = var('mmm_ready_week_start', 'monday') | string | trim | lower -%}
    {%- if day not in weekdays -%}
        {{ exceptions.raise_compiler_error(
            "mmm_ready_week_start must be a weekday name (" ~ weekdays | join(', ') ~ "), got '" ~ day ~ "'") }}
    {%- endif -%}
    {%- set anchor = "cast('1900-01-0" ~ (1 + weekdays.index(day)) ~ "' as date)" -%}
    {%- set d = "cast(" ~ date_expr ~ " as date)" -%}
    cast({{ dbt.dateadd('day', '-1 * mod(' ~ dbt.datediff(anchor, d, 'day') ~ ', 7)', d) }} as date)
{%- endmacro %}
