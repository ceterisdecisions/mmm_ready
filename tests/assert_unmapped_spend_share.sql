{{ config(severity='warn') }}
-- Avisa si la inversión sin mapear supera el umbral: revisa seeds/channel_taxonomy.csv
select *
from {{ ref('mmm_readiness_report') }}
where flag_unmapped_spend = 1
