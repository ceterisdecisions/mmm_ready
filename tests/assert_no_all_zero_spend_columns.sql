-- depends_on: {{ ref('mmm_input_weekly') }}
-- Unless mmm_ready_pivot_all_channels is set, no spend_<channel> column of
-- mmm_input_weekly may sum to zero (spend_unmapped excluded: it always exists).
-- Returns the all-zero columns.
{%- set checks = [] -%}
{%- if execute and not mmm_ready.mmm_ready_as_bool(var('mmm_ready_pivot_all_channels')) -%}
    {%- for col in adapter.get_columns_in_relation(ref('mmm_input_weekly')) -%}
        {%- set name = col.name | lower -%}
        {%- if name.startswith('spend_') and name != 'spend_unmapped' -%}
            {%- do checks.append(name) -%}
        {%- endif -%}
    {%- endfor -%}
{%- endif %}

{% if checks %}
select column_name, total_spend
from (
    {%- for name in checks %}
    select '{{ name }}' as column_name, sum({{ name }}) as total_spend
    from {{ ref('mmm_input_weekly') }}
    {% if not loop.last %}union all{% endif %}
    {%- endfor %}
) t
where total_spend = 0
{% else %}
select 1 as skipped where 1 = 0
{% endif %}
