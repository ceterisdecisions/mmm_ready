select
    cast(date as date)                      as date_day,
    lower(trim(geo))                        as geo,
    cast(revenue as {{ mmm_ready.mmm_ready_type_double() }})      as revenue,
    cast(conversions as {{ dbt.type_bigint() }}) as conversions
{#- mmm_ready_outcomes_source picks the table; the GA4/Firebase adapters return the raw columns. -#}
{#- The macro also rejects unknown values and non-BigQuery targets (at execution). -#}
{%- set outcomes_source = mmm_ready.mmm_ready_outcomes_source() %}
{%- if outcomes_source in ['ga4', 'firebase'] %}
from {{ ref('int_mmm__ga4_outcomes_daily') }}
{%- else %}
from {{ source('mmm_ready_raw', 'outcomes_daily') }}
{%- endif %}
