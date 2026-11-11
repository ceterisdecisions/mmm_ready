# mmm_ready

Open source dbt package that turns marketing data into an **MMM-ready table**
(Meridian, PyMC-Marketing, Robyn) and a **data-quality diagnostic** that tells you whether
that data is fit for modeling.

## What it does
- `fct_marketing_spend_daily`: daily spend by geo and MMM channel, mapped with a canonical taxonomy.
- `fct_outcomes_daily`: daily KPI with price, promotions and holidays.
- `mmm_input_weekly`: wide week × geo table, with one spend and one impressions column per channel.
- `mmm_readiness_report`: per channel, `ready` / `review` / `blocked` (history length, gaps, unmapped spend,
  sparse channels, low variance, spikes, collinearity).
- `mmm_channel_correlations`: spend correlation between channel pairs.

## Local quickstart (DuckDB, zero cost)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cd integration_tests
python ../scripts/generate_synthetic_data.py --out seeds
export DBT_PROFILES_DIR=.
dbt deps && dbt seed && dbt build
```
The synthetic data comes with deliberately seeded issues (see `ground_truth.json`); the readiness report should detect them.

## Using it with your data
Your source tables need these columns:
- `ad_spend_daily`: date, geo, platform, campaign_type, spend, impressions, clicks
- `outcomes_daily`: date, geo, revenue, conversions
- `controls_daily`: date, geo, avg_price, promo_flag

Point to them with variables in your `dbt_project.yml`:
```yaml
vars:
  mmm_ready_raw_schema: marketing_raw
  mmm_ready_ad_spend_identifier: my_ad_spend_table
```
Then extend `seeds/channel_taxonomy.csv` with your platforms and campaign types.

## Notes
- Week: the warehouse's `date_trunc('week')` is used; the week start may vary (BigQuery starts on Sunday).
- Tested on DuckDB. Databricks and Snowflake: not yet validated.

Apache 2.0 License.
