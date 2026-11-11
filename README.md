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

## Channel taxonomy
`seeds/channel_taxonomy.csv` maps each `(platform, campaign_type)` pair to an MMM channel.
Spend with no match lands in `unmapped`, and the readiness report blocks it above the
`mmm_ready_max_unmapped_share` threshold.

| column | meaning |
|---|---|
| `platform`, `campaign_type` | join key against the raw spend data (lowercased); must be unique |
| `mmm_channel` | MMM channel; becomes the `spend_<channel>` / `impressions_<channel>` columns in `mmm_input_weekly` |
| `channel_group` | channel family |
| `demand_capture` | `true` if the channel mainly captures existing demand rather than creating it |

### Naming convention
- `mmm_channel` is `snake_case` and follows `family_qualifier` when a family has several
  variants: `search_brand`, `social_prospecting`, `video_online`, `display_programmatic`, `audio_digital`.
- A channel that is its own family, or that has an established industry name, keeps that single
  name: `tv`, `ctv`, `radio`, `ooh`, `affiliate`, `retail_media`, `performance_max`.
- `channel_group` is the family: `search`, `social`, `video`, `display`, `retail_media`, `audio`, `ooh`, `affiliate`.
- Renaming a channel renames its columns in `mmm_input_weekly`, so treat it as a breaking change.

### Channels
| mmm_channel | channel_group | demand_capture | platform / campaign_type |
|---|---|---|---|
| search_brand | search | true | google_ads, microsoft_ads / search_brand |
| search_nonbrand | search | false | google_ads, microsoft_ads / search_nonbrand |
| search_shopping | search | false | google_ads / shopping |
| performance_max | search | false | google_ads / pmax |
| social_prospecting | social | false | meta, tiktok, linkedin, snapchat / prospecting |
| social_retargeting | social | true | meta / retargeting |
| video_online | video | false | youtube / awareness, performance; dv360 / video |
| ctv | video | false | dv360, the_trade_desk / ctv |
| tv | video | false | tv / linear |
| display_programmatic | display | false | google_ads, dv360, the_trade_desk / display; amazon_ads / dsp |
| retail_media | retail_media | true | amazon_ads / sponsored_products |
| audio_digital | audio | false | spotify / audio |
| radio | audio | false | radio / broadcast |
| ooh | ooh | false | ooh / classic, digital |
| affiliate | affiliate | true | affiliate / network |

### `demand_capture`
Channels flagged `true` (brand search, retargeting, retail media, affiliate) mostly harvest
demand that other channels or the brand already created, so an MMM tends to over-credit them.
Use the flag to give them tighter priors or to report them separately. The flag reaches
`fct_marketing_spend_daily`, where it is `null` for `unmapped` spend because that spend's role is unknown.

## Notes
- Week: the warehouse's `date_trunc('week')` is used; the week start may vary (BigQuery starts on Sunday).
- Tested on DuckDB. Databricks and Snowflake: not yet validated.

Apache 2.0 License.
