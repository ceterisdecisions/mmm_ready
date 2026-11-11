{% macro get_mmm_channels() %}
    {#- List of distinct MMM channels in the taxonomy, for the dynamic pivot. -#}
    {%- if not execute -%}
        {{ return([]) }}
    {%- endif -%}
    {%- set q -%}
        select distinct mmm_channel from {{ ref('channel_taxonomy') }} order by 1
    {%- endset -%}
    {%- set result = run_query(q) -%}
    {{ return(result.columns[0].values() | list) }}
{% endmacro %}
