# mmm_ready

Open source dbt package that turns marketing data into an **MMM-ready table**
(Meridian, PyMC-Marketing, Robyn) and a **data-quality diagnostic** that tells you whether
that data is fit for modeling.

## What it does
- `fct_marketing_spend_daily`: daily spend by geo and MMM channel, mapped with a canonical taxonomy.
- `fct_outcomes_daily`: daily KPI with price, promotions and holidays.
- `mmm_input_weekly`: wide week × geo table, with one spend and one impressions column per channel with spend
  (partial edge weeks dropped, see [Cleaning `mmm_input_weekly`](#cleaning-mmm_input_weekly)).
- `mmm_readiness_report`: per channel, `ready` / `review` / `blocked` (history length, gaps, unmapped spend,
  sparse channels, low variance, spikes, collinearity). Sparsity, variance and spikes are computed over
  complete weeks only: a partial first or last week (fewer than 7 days of spend data) would look like
  real variation.
- `mmm_channel_correlations`: spend correlation between channel pairs, also over complete weeks.
- Optional, BigQuery only: build `outcomes_daily` from a **GA4 or Firebase export**, and split GA4 web
  traffic from **AI assistants** (`ai_referral`). See [GA4 / Firebase adapter](#ga4--firebase-adapter-bigquery-only).

## Local quickstart (DuckDB, zero cost)
```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cd integration_tests
python ../scripts/generate_synthetic_data.py --out seeds
export DBT_PROFILES_DIR=.
dbt deps && dbt seed && dbt build
```
The synthetic data comes with deliberately seeded issues (see `ground_truth.json`); the readiness report should detect them.

To fit a model on the result, see the Meridian smoke test in [`examples/meridian/`](examples/meridian/README.md). It loads `mmm_input_weekly` into Meridian and compares the estimated ROI with the ground truth.

To query the readiness report from an agent (Claude Code or any MCP client), see the MCP server in
[`mcp_server/`](mcp_server/README.md).

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

If your KPI only exists in GA4 or Firebase, the package can build `outcomes_daily` from the
BigQuery export instead: see [GA4 / Firebase adapter](#ga4--firebase-adapter-bigquery-only).

## Channel taxonomy
`seeds/channel_taxonomy.csv` maps each `(platform, campaign_type)` pair to an MMM channel.
Spend with no match lands in `unmapped`, and the readiness report blocks it above the
`mmm_ready_max_unmapped_share` threshold (see [Readiness thresholds](#readiness-thresholds)).

| column | meaning |
|---|---|
| `platform`, `campaign_type` | join key against the raw spend data (lowercased); must be unique |
| `mmm_channel` | MMM channel; becomes the `spend_<channel>` / `impressions_<channel>` columns in `mmm_input_weekly` (only if it has spend, unless `mmm_ready_pivot_all_channels`) |
| `channel_group` | channel family |
| `demand_capture` | `true` if the channel mainly captures existing demand rather than creating it |

### Naming convention
- `mmm_channel` is `snake_case` and follows `family_qualifier` when a family has several
  variants: `search_brand`, `social_prospecting`, `video_online`, `display_programmatic`, `audio_digital`,
  `ai_assistant`.
- A channel that is its own family, or that has an established industry name, keeps that single
  name: `tv`, `ctv`, `radio`, `ooh`, `affiliate`, `retail_media`, `performance_max`.
- `channel_group` is the family: `search`, `social`, `video`, `display`, `retail_media`, `audio`, `ooh`, `affiliate`, `ai`.
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
| ai_assistant | ai | false | openai / chatgpt_ads; perplexity / sponsored |

### Ads in AI search answers
`ai_assistant` covers ads bought directly on AI assistants (ChatGPT, Perplexity). Google's ads in
AI Overviews and AI Mode are not a separate buy: they are served from existing Google Ads campaigns
(Search, Shopping, Performance Max) and their spend cannot be split out from those campaigns. That spend
therefore stays in `search_brand`, `search_nonbrand`, `search_shopping` and `performance_max`, not in
`ai_assistant`.

### `demand_capture`
Channels flagged `true` (brand search, retargeting, retail media, affiliate) mostly harvest
demand that other channels or the brand already created, so an MMM tends to over-credit them.
Use the flag to give them tighter priors or to report them separately. The flag reaches
`fct_marketing_spend_daily`, where it is `null` for `unmapped` spend because that spend's role is unknown.

## Cleaning `mmm_input_weekly`
Two vars control how the MMM input table is trimmed:

| var | default | effect |
|---|---|---|
| `mmm_ready_drop_partial_weeks` | `true` | Drops the first and the last week when they have fewer than 7 days of outcomes. Days are counted across the whole panel, so every geo keeps the same weeks. Gaps in the middle of the series are not dropped: the readiness report blocks them (`flag_kpi_gaps`). |
| `mmm_ready_pivot_all_channels` | `false` | By default only channels with total spend > 0 get `spend_<channel>` / `impressions_<channel>` columns, so the MMM never sees all-zero columns. Set it to `true` to get one column per taxonomy channel. `spend_unmapped` is always present. |

```yaml
vars:
  mmm_ready_drop_partial_weeks: false
  mmm_ready_pivot_all_channels: true
```

The set of channel columns now depends on the data: a channel with spend gains a column on the next
build. If a downstream job expects a fixed schema, set `mmm_ready_pivot_all_channels: true`.
Which edge weeks are partial depends on `mmm_ready_week_start` (see Notes).

The readiness report always reads the full, untrimmed data.

## Readiness thresholds
Every check in `mmm_readiness_report` compares one column of the report with an `mmm_ready_*` var.
The defaults live in the package's root `dbt_project.yml`; override them under `vars:` in your own
project or with `--vars`. **The defaults are heuristics, not statistical standards**: none of them
comes from a measured study. Each one is an initial heuristic, adjustable. Tune them to your data
and your model, and don't read a `ready` as proof that a channel can be estimated.

An **issue** that blocks makes the channel `blocked`. A **review** issue makes it `review`. A
**warning** is informational and never changes `readiness_status`. All comparisons are strict
(`>` or `<`) except collinearity, which uses `>=`.

| var | default | what it checks (report column) | severity | when to change it |
|---|---|---|---|---|
| `mmm_ready_min_history_weeks` | `104` | `flag_short_history` when `expected_weeks < 104` | issue (blocking) | If your model or your business cycle needs more or less history. The value used is stored in the `min_history_weeks` column. |
| `mmm_ready_max_unmapped_share` | `0.02` | `flag_unmapped_spend` when the `spend_share` of `unmapped` is `> 0.02` | issue (blocking) | If you accept more spend outside the taxonomy, or want zero tolerance. The better fix is usually to extend `channel_taxonomy.csv`. |
| `mmm_ready_max_kpi_missing_weeks` | `0` | `flag_kpi_gaps` when `kpi_missing_weeks > 0` (weeks with no revenue between the first and last week of spend) | issue (blocking) | If your model handles a few missing KPI weeks (for example by imputation) and you prefer not to block on them. |
| `mmm_ready_max_platform_gap_weeks` | `0` | `flag_platform_gaps` when `max_platform_gap_weeks > 0`: the largest gap of any single platform, in weeks with no rows between its first and last active week | review | If some platforms pause by design (flighting, seasonal campaigns) and you have confirmed that the gaps are not data loss. |
| `mmm_ready_max_zero_week_share` | `0.5` | `flag_sparse` when `zero_week_share > 0.5` (share of complete weeks without spend) | review | If you model occasional channels on purpose, or want to flag them earlier. |
| `mmm_ready_min_cv` | `0.1` | `flag_low_variance` when `cv_weekly_spend < 0.1` (weekly spend std / mean, complete weeks) | review | If your always-on channels are flagged and you already calibrate them with experiments, or to flag more of them. |
| `mmm_ready_max_spike_ratio` | `5` | `flag_spike` when `spike_ratio > 5` (max week / mean week, complete weeks) | review | If your spend has planned bursts (launches, seasonal peaks) and you have checked them against billing. |
| `mmm_ready_max_corr` | `0.8` | `flag_collinear` when `max_abs_corr_with_other >= 0.8` (abs weekly spend correlation with any other channel, complete weeks, `unmapped` excluded); same `>=` in `mmm_channel_correlations.is_collinear` | review | If your model groups or regularizes correlated channels, or to catch weaker correlations. |
| — | — | `warn_kpi_from_analytics = 1` when `mmm_ready_outcomes_source` is `ga4` or `firebase` (see [Readiness warning](#readiness-warning)) | warning | It has no threshold: it depends only on where the KPI comes from. |

Notes:
- A channel's gap is measured per platform: `flag_platform_gaps` compares the largest gap of any of
  its platforms (`max_platform_gap_weeks`) with the threshold. `gap_weeks` is the sum of the gaps of
  all its platforms and is informational only.
- Partial weeks are not a threshold. A first or last week with fewer than 7 days is partial by
  definition and is fixed in the code. Per-week statistics (sparsity, variance, spikes,
  correlations) skip those weeks.

```yaml
vars:
  mmm_ready_max_platform_gap_weeks: 4   # e.g. TV is flighted: accept pauses of up to 4 weeks
```

## GA4 / Firebase adapter (BigQuery only)
An optional adapter builds `outcomes_daily` (date, geo, revenue, conversions) from the GA4 BigQuery
export, for websites (`ga4`) or apps (`firebase`: Firebase apps use the same export format).
Everything downstream (`fct_outcomes_daily`, `mmm_input_weekly`, the readiness report) is unchanged.
It is off by default, and it is the only BigQuery-specific part of the package: the export exists
only in BigQuery, as daily `events_YYYYMMDD` tables. On any other warehouse it refuses to compile.
`controls_daily` and `ad_spend_daily` still come from your own tables.

### Turning it on
```yaml
vars:
  mmm_ready_outcomes_source: ga4              # raw (default) | ga4 | firebase
  mmm_ready_ga4_project: my-gcp-project       # project of the export (default: target project)
  mmm_ready_ga4_dataset: analytics_123456789
  mmm_ready_ga4_start_date: '2023-01-01'      # required
  mmm_ready_ga4_end_date: '2025-12-31'        # required
  mmm_ready_ga4_geo_field: country            # match the geo values of your spend data
  mmm_ready_ga4_countries: [Spain]
```

| var | default | effect |
|---|---|---|
| `mmm_ready_outcomes_source` | `raw` | `ga4` or `firebase` builds `outcomes_daily` from the export. |
| `mmm_ready_ga4_project`, `mmm_ready_ga4_dataset` | target project, — | Where the `events_*` tables are. |
| `mmm_ready_ga4_start_date`, `mmm_ready_ga4_end_date` | — (required) | `YYYY-MM-DD`. They bound the `_TABLE_SUFFIX` filter, so the build never scans the whole export. |
| `mmm_ready_ga4_conversion_events` | `[purchase]` / `[in_app_purchase]` | Event names counted as conversions (list or comma-separated string). |
| `mmm_ready_ga4_revenue_field` | `purchase_revenue` / `event_value_in_usd` | `ga4`: `purchase_revenue` (property currency), `purchase_revenue_in_usd` or `event_value_in_usd`. `firebase`: only `event_value_in_usd`. |
| `mmm_ready_ga4_geo_field` | `region` | `country`, `region` or `city` of `geo`. `geo` is lowercased in staging, and it must match the geos of your spend and controls tables. |
| `mmm_ready_ga4_countries` | `[]` (all) | Keep only these `geo.country` values, e.g. `[Spain]`. |
| `mmm_ready_ga4_dedupe_transactions` | `true` | Count each conversion once. GA4: by `transaction_id`, as GA4 reports do. Firebase has no `transaction_id`, so only exact repeats (same user and timestamp) are dropped. |
| `mmm_ready_ga4_ai_referral` | `false` | Also build `int_mmm__ga4_ai_referral_daily` (GA4 web only, see below). |

Each (day, geo) with any event is in the output, so a day with traffic but no purchases is 0, not
a missing row. Dates are `event_date`, in the property's reporting time zone.

### Cost and date filter
BigQuery on-demand pricing bills by bytes scanned. The adapter always filters `events_*` by
`_TABLE_SUFFIX` between the two required dates, and reads only the columns it needs: never
`event_params`, the largest one. The partition filter also excludes the `events_intraday_*` tables.
Scans measured on the public samples: 183 MiB for 92 days of GA4 web, 441 MiB for 114 days of the
Firebase app. A real property scans much more per day, so start with a short date range and check
the bytes processed that dbt prints. `int_mmm__ga4_ai_referral_daily` reads `event_params` and
costs about 9x the outcomes model (1.6 GiB on the GA4 sample), which is why it has its own toggle.

### AI-assistant traffic (`ai_referral`)
With `mmm_ready_ga4_ai_referral: true`, `int_mmm__ga4_ai_referral_daily` gives sessions, conversions
and revenue per day, geo and `traffic_source_group`: `ai_referral` or `other`, with the assistant
in `ai_assistant`. A session is `ai_referral` when its landing page's referrer host, or else its
`utm_source` (ChatGPT adds `utm_source=chatgpt.com`), is a domain of
[`seeds/ai_referral_domains.csv`](seeds/ai_referral_domains.csv) or a subdomain of one. The seed holds
the main assistants only: chatgpt.com, chat.openai.com, perplexity.ai, gemini.google.com,
copilot.microsoft.com, claude.ai.

This traffic is unpaid, so it is a separate table to read next to the MMM, not a channel in
`mmm_input_weekly`. It is unrelated to the paid `ai_assistant` channel (ads bought on ChatGPT or
Perplexity). It reads only `event_params` (`page_referrer`, `page_location`, `ga_session_id`), which
exist in every export version. It is web only: app events have no referrer.

### Readiness warning
When the KPI comes from GA4 or Firebase, `mmm_readiness_report` sets `kpi_source` and
`warn_kpi_from_analytics = 1`. Analytics undercounts revenue (consent banners, ad blockers,
cross-device journeys) and the gap can drift over time, so reconcile it against the ERP or backend
before trusting the model. The warning is informational: it never changes `readiness_status`. The
MCP server shows it as a `warning`.

### Running it on BigQuery
`integration_tests` has an opt-in `bigquery` target (DuckDB stays the default and the CI target):
```bash
pip install -r requirements-bigquery.txt
gcloud auth application-default login
export MMM_READY_BQ_PROJECT=<your-gcp-project>   # required, no default
export DBT_PROFILES_DIR=.                         # from integration_tests/
dbt seed --target bigquery
dbt build --target bigquery --exclude "package:mmm_ready_integration_tests,resource_type:seed"
```
Without `MMM_READY_BQ_PROJECT`, dbt stops with `Env var required but not provided`. The dataset
defaults to `mmm_ready_dev` (`MMM_READY_BQ_DATASET`) and is created in the `US` location, where the
public samples are, since BigQuery cannot query across locations. Every query is capped at
10 GB billed. The `--exclude` keeps `build` from reloading the raw seeds while the models read
them. The commands for the GA4, Firebase and AI-referral test runs are in `CLAUDE.md`.

### Limitations of the public datasets
The adapter is validated against two public exports. Neither one can test an MMM:
- **GA4** `bigquery-public-data.ga4_obfuscated_sample_ecommerce`: only 92 days (2020-11-01 to
  2021-01-31), obfuscated, with no AI-assistant traffic (the classification is tested on a small
  sample export instead). Its 2020 schema lacks `collected_traffic_source` and
  `session_traffic_source_last_click`.
- **Firebase** `firebase-public-project.analytics_153293282`: 2018-06-12 to 2018-10-03, an old schema
  without `ecommerce`, and only 27 `in_app_purchase` events (24.89 USD) in total.
- Neither has marketing spend. The test runs pair them with random spend from
  `scripts/generate_ga4_fixture.py`, and with under two years of history the readiness report
  rightly blocks every channel (`flag_short_history`).

## Notes
- Week: weeks start on Monday on every warehouse. Set `mmm_ready_week_start` to another weekday name
  (`sunday`, ...) to change it. The package does not use `date_trunc('week')`, whose week start
  differs between warehouses (BigQuery starts on Sunday), so DuckDB and BigQuery return the same weeks.
- Tested on DuckDB (CI) and BigQuery (manually). Databricks and Snowflake: not yet validated.

Apache 2.0 License.
