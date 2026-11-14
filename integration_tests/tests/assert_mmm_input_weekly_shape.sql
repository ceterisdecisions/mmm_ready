{{ config(enabled = (var('mmm_ready_outcomes_source', 'raw') | string | trim | lower) == 'raw') }}
-- depends_on: {{ ref('mmm_input_weekly') }}
-- Exact shape of mmm_input_weekly on the synthetic data, for any mmm_ready_week_start
-- (synthetic outcomes only: disabled when the KPI comes from the GA4/Firebase adapter).
-- Data runs 2023-01-01 (Sunday) .. 2025-12-31 (Wednesday). Expected edge weeks are derived
-- here in Python date arithmetic, independently of the SQL macro: with the default Monday
-- weeks both edges are partial (first kept week 2023-01-02, last 2025-12-22).
-- ctv has no spend in the synthetic data: its column exists only with pivot_all_channels.
-- Package vars are not visible from this project unless passed with --vars, so the
-- defaults below must match the root dbt_project.yml.
{%- set drop = mmm_ready.mmm_ready_as_bool(var('mmm_ready_drop_partial_weeks', true)) -%}
{%- set pivot_all = mmm_ready.mmm_ready_as_bool(var('mmm_ready_pivot_all_channels', false)) -%}
{%- set weekdays = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'] -%}
{%- set day = var('mmm_ready_week_start', 'monday') | string | trim | lower -%}
{#- An invalid value is reported by the mmm_ready_week_start macro; don't fail here first. -#}
{%- set wd = weekdays.index(day) if day in weekdays else 0 -%}
{%- set dt = modules.datetime -%}
{%- set data_start = dt.date(2023, 1, 1) -%}
{%- set data_end = dt.date(2025, 12, 31) -%}
{%- set first = data_start - dt.timedelta(days=(data_start.weekday() - wd) % 7) -%}
{%- set last = data_end - dt.timedelta(days=(data_end.weekday() - wd) % 7) -%}
{%- if drop and first < data_start -%}{%- set first = first + dt.timedelta(days=7) -%}{%- endif -%}
{%- if drop and (data_end - last).days < 6 -%}{%- set last = last - dt.timedelta(days=7) -%}{%- endif -%}
{%- set has_ctv = false -%}
{%- if execute -%}
    {%- set names = adapter.get_columns_in_relation(ref('mmm_input_weekly')) | map(attribute='name') | map('lower') | list -%}
    {%- set has_ctv = 'spend_ctv' in names -%}
{%- endif %}

select 'week range' as failure, min(week_start) as first_week, max(week_start) as last_week
from {{ ref('mmm_input_weekly') }}
having min(week_start) <> cast('{{ first }}' as date)
    or max(week_start) <> cast('{{ last }}' as date)

union all

select 'spend_ctv column' as failure, cast(null as date), cast(null as date)
from (select 1 as one) x
where {{ 1 if has_ctv != pivot_all else 0 }} = 1
