{{ config(severity='warn') }}
-- Warns if unmapped spend exceeds the threshold: check seeds/channel_taxonomy.csv
select *
from {{ ref('mmm_readiness_report') }}
where flag_unmapped_spend = 1
