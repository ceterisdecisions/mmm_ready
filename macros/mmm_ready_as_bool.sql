{% macro mmm_ready_as_bool(value) %}
    {#- Normalizes a var to a boolean. Values passed with --vars can arrive as
        strings, and the string "false" would otherwise be truthy in Jinja. -#}
    {%- if value is string -%}
        {{ return(value | trim | lower in ['true', '1', 'yes', 'y']) }}
    {%- endif -%}
    {{ return(value is sameas true or value == 1) }}
{% endmacro %}
