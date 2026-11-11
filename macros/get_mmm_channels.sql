{% macro get_mmm_channels() %}
    {#- MMM channels for the dynamic pivot in mmm_input_weekly.
        Default: channels with total spend > 0 in the data ('unmapped' excluded,
        it always gets its own column). With mmm_ready_pivot_all_channels, or if
        the spend view does not exist yet (e.g. compiling before the first build),
        every channel in the taxonomy. -#}
    {%- if not execute -%}
        {{ return([]) }}
    {%- endif -%}
    {%- set spend_ref = ref('int_mmm__spend_weekly') -%}
    {%- set spend_rel = adapter.get_relation(
            database=spend_ref.database,
            schema=spend_ref.schema,
            identifier=spend_ref.identifier) -%}
    {%- if mmm_ready.mmm_ready_as_bool(var('mmm_ready_pivot_all_channels')) or spend_rel is none -%}
        {%- set q -%}
            select distinct mmm_channel from {{ ref('channel_taxonomy') }} order by 1
        {%- endset -%}
    {%- else -%}
        {%- set q -%}
            select mmm_channel
            from {{ spend_rel }}
            where mmm_channel <> 'unmapped'
            group by mmm_channel
            having sum(spend) > 0
            order by 1
        {%- endset -%}
    {%- endif -%}
    {%- set result = run_query(q) -%}
    {{ return(result.columns[0].values() | list) }}
{% endmacro %}
