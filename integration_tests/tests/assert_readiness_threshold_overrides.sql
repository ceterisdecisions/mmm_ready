{#-
  Threshold overrides change the readiness report as expected, on the synthetic data.
  Enabled only in the CI step that passes these vars (default week start, monday):
    mmm_ready_it_threshold_overrides: true
    mmm_ready_min_history_weeks: 158      # = expected_weeks: strict <, nothing flagged
    mmm_ready_max_unmapped_share: 0.10    # Pinterest is ~8.5%: unmapped no longer blocked
    mmm_ready_max_platform_gap_weeks: 3   # TikTok gap is 3 weeks: strict >, not flagged; TV/YouTube still are
    mmm_ready_max_spike_ratio: 8          # Meta prospecting spike is ~7.5x: not flagged
    mmm_ready_max_corr: 0.6               # search/retargeting pairs (~0.62-0.66) become collinear
  Returns channels whose flags or status differ from the expectation, or that are missing/extra.
-#}
{%- set on = (var('mmm_ready_it_threshold_overrides', false) | string | trim | lower) in ['true', '1', 'yes'] -%}
{{ config(enabled = on and (var('mmm_ready_outcomes_source', 'raw') | string | trim | lower) == 'raw') }}

{#- channel: (short_history, unmapped_spend, platform_gaps, spike, collinear, status) -#}
{%- set expected = {
    'search_brand':       (0, 0, 0, 0, 1, 'review'),
    'search_nonbrand':    (0, 0, 0, 0, 1, 'review'),
    'social_prospecting': (0, 0, 0, 0, 0, 'ready'),
    'social_retargeting': (0, 0, 0, 0, 1, 'review'),
    'tv':                 (0, 0, 1, 0, 1, 'review'),
    'unmapped':           (0, 0, 0, 0, 0, 'review'),
    'video_online':       (0, 0, 1, 0, 1, 'review'),
} %}

with expected as (
    {%- for ch, e in expected.items() %}
    select '{{ ch }}' as mmm_channel, 158 as expected_weeks,
           {{ e[0] }} as flag_short_history, {{ e[1] }} as flag_unmapped_spend,
           {{ e[2] }} as flag_platform_gaps, {{ e[3] }} as flag_spike,
           {{ e[4] }} as flag_collinear, '{{ e[5] }}' as readiness_status
    {% if not loop.last %}union all{% endif %}
    {%- endfor %}
),

actual as (
    select mmm_channel, expected_weeks, flag_short_history, flag_unmapped_spend,
           flag_platform_gaps, flag_spike, flag_collinear, readiness_status
    from {{ ref('mmm_readiness_report') }}
)

select e.mmm_channel, 'mismatch or missing' as problem
from expected e
left join actual a on e.mmm_channel = a.mmm_channel
where a.mmm_channel is null
   or a.expected_weeks      <> e.expected_weeks
   or a.flag_short_history  <> e.flag_short_history
   or a.flag_unmapped_spend <> e.flag_unmapped_spend
   or a.flag_platform_gaps  <> e.flag_platform_gaps
   or a.flag_spike          <> e.flag_spike
   or a.flag_collinear      <> e.flag_collinear
   or a.readiness_status    <> e.readiness_status

union all

select a.mmm_channel, 'unexpected channel' as problem
from actual a
left join expected e on a.mmm_channel = e.mmm_channel
where e.mmm_channel is null
