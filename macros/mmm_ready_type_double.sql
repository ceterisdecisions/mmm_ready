{#-
    64-bit floating point type. dbt.type_float() renders "float", which is 64-bit on
    BigQuery, Snowflake and Postgres but 32-bit (~7 significant digits) on DuckDB and
    Spark/Databricks: spend and revenue lost precision there and results differed
    between warehouses.
-#}
{% macro mmm_ready_type_double() -%}
    {{ return(adapter.dispatch('mmm_ready_type_double', 'mmm_ready')()) }}
{%- endmacro %}

{% macro default__mmm_ready_type_double() -%}
    {{ return(dbt.type_float()) }}
{%- endmacro %}

{% macro duckdb__mmm_ready_type_double() -%}
    {{ return('double') }}
{%- endmacro %}

{% macro spark__mmm_ready_type_double() -%}
    {{ return('double') }}
{%- endmacro %}

{% macro databricks__mmm_ready_type_double() -%}
    {{ return('double') }}
{%- endmacro %}
