-- depends_on: {{ ref('mmm_input_weekly') }}
-- Exact shape of mmm_input_weekly on the synthetic data, for the two tested warehouses.
-- Data runs 2023-01-01 (Sunday) .. 2025-12-31 (Wednesday).
-- DuckDB weeks start on Monday: both edge weeks are partial.
-- BigQuery weeks start on Sunday: the first week is complete, only the last is partial.
-- ctv has no spend in the synthetic data: its column exists only with pivot_all_channels.
-- Package vars are not visible from this project unless passed with --vars, so the
-- defaults below must match the root dbt_project.yml.
{%- set drop = mmm_ready.mmm_ready_as_bool(var('mmm_ready_drop_partial_weeks', true)) -%}
{%- set pivot_all = mmm_ready.mmm_ready_as_bool(var('mmm_ready_pivot_all_channels', false)) -%}
{%- if target.type == 'bigquery' -%}
    {%- set expected_first = '2023-01-01' -%}
    {%- set expected_last = '2025-12-21' if drop else '2025-12-28' -%}
{%- else -%}
    {%- set expected_first = '2023-01-02' if drop else '2022-12-26' -%}
    {%- set expected_last = '2025-12-22' if drop else '2025-12-29' -%}
{%- endif -%}
{%- set has_ctv = false -%}
{%- if execute -%}
    {%- set names = adapter.get_columns_in_relation(ref('mmm_input_weekly')) | map(attribute='name') | map('lower') | list -%}
    {%- set has_ctv = 'spend_ctv' in names -%}
{%- endif %}

select 'week range' as failure, min(week_start) as first_week, max(week_start) as last_week
from {{ ref('mmm_input_weekly') }}
having min(week_start) <> cast('{{ expected_first }}' as date)
    or max(week_start) <> cast('{{ expected_last }}' as date)

union all

select 'spend_ctv column' as failure, cast(null as date), cast(null as date)
from (select 1 as one) x
where {{ 1 if has_ctv != pivot_all else 0 }} = 1
