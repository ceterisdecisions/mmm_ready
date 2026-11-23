-- Every flag of mmm_readiness_report must follow from its metric column and the current
-- mmm_ready_* threshold var, whatever values the vars have (defaults or --vars overrides),
-- and readiness_status must follow from the flags. Returns the inconsistent channels.
with expected as (
    select
        mmm_channel,
        flag_unmapped_spend,
        flag_short_history,
        flag_kpi_gaps,
        flag_platform_gaps,
        flag_sparse,
        flag_low_variance,
        flag_spike,
        flag_collinear,
        readiness_status,
        min_history_weeks,
        case when mmm_channel = 'unmapped'
              and spend_share > {{ var('mmm_ready_max_unmapped_share') }}             then 1 else 0 end as exp_unmapped_spend,
        case when expected_weeks < {{ var('mmm_ready_min_history_weeks') }}          then 1 else 0 end as exp_short_history,
        case when kpi_missing_weeks > {{ var('mmm_ready_max_kpi_missing_weeks') }}   then 1 else 0 end as exp_kpi_gaps,
        case when max_platform_gap_weeks > {{ var('mmm_ready_max_platform_gap_weeks') }} then 1 else 0 end as exp_platform_gaps,
        case when zero_week_share > {{ var('mmm_ready_max_zero_week_share') }}       then 1 else 0 end as exp_sparse,
        case when cv_weekly_spend < {{ var('mmm_ready_min_cv') }}                    then 1 else 0 end as exp_low_variance,
        case when spike_ratio > {{ var('mmm_ready_max_spike_ratio') }}               then 1 else 0 end as exp_spike,
        -- >= on purpose: same operator as mmm_channel_correlations.is_collinear
        case when max_abs_corr_with_other >= {{ var('mmm_ready_max_corr') }}         then 1 else 0 end as exp_collinear
    from {{ ref('mmm_readiness_report') }}
)

select *
from expected
where flag_unmapped_spend <> exp_unmapped_spend
   or flag_short_history  <> exp_short_history
   or flag_kpi_gaps       <> exp_kpi_gaps
   or flag_platform_gaps  <> exp_platform_gaps
   or flag_sparse         <> exp_sparse
   or flag_low_variance   <> exp_low_variance
   or flag_spike          <> exp_spike
   or flag_collinear      <> exp_collinear
   or min_history_weeks   <> {{ var('mmm_ready_min_history_weeks') }}
   or readiness_status <> case
        when exp_unmapped_spend + exp_short_history + exp_kpi_gaps > 0 then 'blocked'
        when exp_platform_gaps + exp_sparse + exp_low_variance + exp_spike + exp_collinear > 0 then 'review'
        else 'ready'
      end
