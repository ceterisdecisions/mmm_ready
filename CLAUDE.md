# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`mmm_ready` is a dbt **package** (not a standalone project) that turns raw marketing data into a wide weekly table ready for Marketing Mix Modeling (Meridian, PyMC-Marketing, Robyn), plus a data-quality diagnostic (`mmm_readiness_report`) that labels each channel `ready` / `review` / `blocked`. The whole repo is in English: docs, code comments, model/source descriptions, log messages and commit messages — keep that convention. (Seed data values such as Spanish holiday names are data, not prose, and stay as-is.)

The root has no `profiles.yml`/profile; it's consumed by other projects. Development and CI run through `integration_tests/`, a separate dbt project that installs the package via `packages.yml` (`local: ../`) and runs on DuckDB.

## Project rules

- Warehouse-agnostic dbt package: use only dbt cross-database macros (`dbt.datediff`, `dbt.dateadd`, `dbt.listagg`, `dbt.type_*`...) and standard SQL. Two package macros replace dbt ones whose behavior differs by warehouse: `mmm_ready_week_start` instead of `dbt.date_trunc('week', ...)` and `mmm_ready_type_double` instead of `dbt.type_float()` (see Architecture).
- **One accepted exception: the GA4/Firebase adapter** (`models/adapters/ga4/`, `macros/ga4_adapter.sql`) is BigQuery-only, because those exports exist only in BigQuery (`events_*` wildcard, `_TABLE_SUFFIX`, `qualify`, `parse_date`, struct fields). It must stay isolated there, disabled by default (`mmm_ready_outcomes_source: raw`), and fail at compile time on any other target. Nothing outside the adapter may depend on BigQuery.
- No external dependencies (not even `dbt_utils`): if a macro is needed, write it in `macros/`.
- Every change must pass `dbt build` in `integration_tests/` on DuckDB before it counts as done.
- If you change the synthetic generator, update `ground_truth.json` (the script writes it, so keep its `seeded_issues` list current) and verify the readiness report still detects the 4 seeded issues.
- Never reuse code or logic from client projects.
- Before large changes, explain the plan and wait for approval.
- Small commits with clear messages.

## Commands

All dbt commands run from `integration_tests/` with `DBT_PROFILES_DIR=.`:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cd integration_tests
python ../scripts/generate_synthetic_data.py --out seeds   # optional: --seed 42
export DBT_PROFILES_DIR=.
dbt deps && dbt seed && dbt build
```

- Single model + its tests: `dbt build --select mmm_readiness_report`
- Model and downstream: `dbt build --select int_mmm__spend_mapped+`
- Tests only: `dbt test --select mmm_input_weekly`
- Inspect results: DuckDB file is `integration_tests/mmm_ready_dev.duckdb`.
- BigQuery (opt-in, not in CI): `pip install -r requirements-bigquery.txt`, auth with `gcloud auth application-default login`, then `dbt seed --target bigquery` followed by `dbt build --target bigquery --exclude "package:mmm_ready_integration_tests,resource_type:seed"`. The exclude matters: the raw tables are `source`s, so dbt doesn't order them after their seeds, and reloading them inside `build` races with the staging models (BigQuery drops and recreates the table). The GCP project has no default (public repo): `export MMM_READY_BQ_PROJECT=<your-project>`, otherwise dbt stops with `Env var required but not provided: 'MMM_READY_BQ_PROJECT'`; DuckDB runs don't need it, because dbt renders only the selected target. Dataset defaults to `mmm_ready_dev` (`MMM_READY_BQ_DATASET`); location is pinned to `US`.

CI (`.github/workflows/ci.yml`) runs exactly: generate data → `dbt deps` → `dbt seed` → `dbt build` → `dbt build --vars '{mmm_ready_drop_partial_weeks: false, mmm_ready_pivot_all_channels: true, mmm_ready_week_start: sunday}'` on Python 3.11, both builds with `--exclude "package:mmm_ready_integration_tests,resource_type:seed"` (see the BigQuery note above) → a guard that `mmm_ready_outcomes_source: ga4|firebase` and `mmm_ready_ga4_ai_referral: true` fail to compile on DuckDB with "needs a BigQuery target" → MCP server tests, which read the DuckDB file left by the second build. A new boolean (or enum) var should be added to that second build so CI covers its non-default branch.

## Architecture

Layered pipeline (`models/`): `staging` (views) → `intermediate` (views) → `marts` (tables) → `diagnostics` (tables).

- **Sources** (`models/staging/_sources.yml`): three raw tables (`ad_spend_daily`, `outcomes_daily`, `controls_daily`) whose database/schema/identifier are all overridable via `mmm_ready_raw_*` vars. In integration tests these are the generated seeds. Staging lowercases/trims `geo`, `platform`, `campaign_type` and casts with cross-db `dbt.type_*()` macros, except floats: use `mmm_ready.mmm_ready_type_double()`, because `dbt.type_float()` is 32-bit on DuckDB and Spark/Databricks. Required raw columns (selected by name, so a mismatch breaks the build):
  - `ad_spend_daily`: date, geo, platform, campaign_type, spend, impressions, clicks
  - `outcomes_daily`: date, geo, revenue, conversions
  - `controls_daily`: date, geo, avg_price, promo_flag
- **Outcomes source** (`stg_mmm__outcomes`): `mmm_ready_outcomes_source` (macro `mmm_ready_outcomes_source()`) picks `raw` (the `outcomes_daily` source, default) or the adapter `int_mmm__ga4_outcomes_daily` (table) for `ga4` (web) and `firebase` (apps: same export format). The adapter returns the raw columns (date, geo, revenue, conversions), so nothing downstream changes; controls still come from the raw `controls_daily`. It reads `source('mmm_ready_ga4', 'events')` (`events_*` in `mmm_ready_ga4_project`/`_dataset`), always filtered with `mmm_ready_ga4_table_suffix_filter()` from the required `mmm_ready_ga4_start_date`/`_end_date`, and never reads `event_params` (~4-10x the bytes). Per-source differences live in macros:
  - conversions `mmm_ready_ga4_conversion_events`: default `purchase` (ga4) / `in_app_purchase` (firebase);
  - revenue `mmm_ready_ga4_revenue_field`: default `purchase_revenue` (ga4) / `event_value_in_usd` (firebase, the only one allowed: the 2018 schema has no `ecommerce`);
  - dedupe key `mmm_ready_ga4_dedupe_key_expr()`: `transaction_id` (ga4; events without one are all kept) / user + timestamp (firebase: exact repeats only). Earliest event kept.
  Every (day, geo) with any event is in the panel, so days without conversions are 0. `integration_tests/tests/assert_ga4_outcomes_reconcile.sql` (enabled for ga4/firebase) recomputes conversions, revenue and the panel independently of the model. The synthetic spend (2023-2025, Spanish regions) overlaps neither public sample, so these runs use `scripts/generate_ga4_fixture.py --window ga4|firebase` (random spend and controls for the sample's dates and top countries, no planted effects; kept apart from the synthetic generator so its CSVs and `ground_truth.json` don't change). Both full runs must pass (readiness is `blocked` only by `flag_short_history`):
  ```bash
  # GA4: bigquery-public-data.ga4_obfuscated_sample_ecommerce, 2020-11-01..2021-01-31, ~190 MB
  python ../scripts/generate_ga4_fixture.py --out seeds
  dbt seed --target bigquery --select ga4_ad_spend_daily ga4_controls_daily
  dbt build --target bigquery --exclude "package:mmm_ready_integration_tests,resource_type:seed" --vars '{mmm_ready_outcomes_source: ga4, mmm_ready_ga4_project: bigquery-public-data, mmm_ready_ga4_dataset: ga4_obfuscated_sample_ecommerce, mmm_ready_ga4_start_date: 2020-11-01, mmm_ready_ga4_end_date: 2021-01-31, mmm_ready_ga4_geo_field: country, mmm_ready_ga4_countries: [United States, Canada, India], mmm_ready_ad_spend_identifier: ga4_ad_spend_daily, mmm_ready_controls_identifier: ga4_controls_daily}'
  # Firebase: firebase-public-project.analytics_153293282, 2018-06-12..2018-10-03, ~440 MB.
  # Only 27 in_app_purchase events (24.89 USD) in the whole sample: fine for testing, useless as an MMM KPI.
  python ../scripts/generate_ga4_fixture.py --out seeds --window firebase
  dbt seed --target bigquery --select firebase_ad_spend_daily firebase_controls_daily
  dbt build --target bigquery --exclude "package:mmm_ready_integration_tests,resource_type:seed" --vars '{mmm_ready_outcomes_source: firebase, mmm_ready_ga4_project: firebase-public-project, mmm_ready_ga4_dataset: analytics_153293282, mmm_ready_ga4_start_date: 2018-06-12, mmm_ready_ga4_end_date: 2018-10-03, mmm_ready_ga4_geo_field: country, mmm_ready_ga4_countries: [United States, Japan, Australia], mmm_ready_ad_spend_identifier: firebase_ad_spend_daily, mmm_ready_controls_identifier: firebase_controls_daily}'
  ```
- **AI-assistant traffic** (`int_mmm__ga4_ai_referral_daily`, table): own toggle `mmm_ready_ga4_ai_referral` (default `false`), GA4 web only (rejected with `firebase`: apps have no referrer), same `mmm_ready_ga4_*` export/date/country vars as the outcomes adapter, whatever `mmm_ready_outcomes_source` is. Unpaid traffic, so a separate table with `traffic_source_group` = `ai_referral` | `other` (not `ai_assistant`, which is the paid channel in the taxonomy) and `ai_assistant`; never a column of `mmm_input_weekly`. A session (`user_pseudo_id` + `ga_session_id`) is classified by its landing page (first event with `page_location`): its referrer host, else its `utm_source`, equal to or a subdomain of a domain in `seeds/ai_referral_domains.csv`. That seed has exactly 6 domains (chatgpt.com, chat.openai.com, perplexity.ai, gemini.google.com, copilot.microsoft.com, claude.ai): keep it to the main assistants, don't add more without asking. It reads `event_params` (only place in the package; ~9x the outcomes adapter's bytes, 1.6 GiB on the GA4 sample), and only `event_params`, because `collected_traffic_source`/`session_traffic_source_last_click` don't exist in older exports. Conversions are deduplicated like the outcomes adapter, so `integration_tests/tests/assert_ga4_ai_referral_totals.sql` checks that per-day totals match it. The public sample has no AI traffic (all `other`), so the classification is tested on sample data: `dbt run-operation create_ga4_ai_fixture --target bigquery` (in `integration_tests/macros/`) writes `<schema>_ga4_fixture.events_20250106/07`, and `tests/assert_ga4_ai_referral_fixture.sql` checks the exact output:
  ```bash
  dbt run-operation create_ga4_ai_fixture --target bigquery
  dbt build --target bigquery --exclude "package:mmm_ready_integration_tests,resource_type:seed" --vars '{mmm_ready_ga4_ai_referral: true, mmm_ready_it_ai_fixture: true, mmm_ready_ga4_dataset: mmm_ready_dev_ga4_fixture, mmm_ready_ga4_start_date: 2025-01-06, mmm_ready_ga4_end_date: 2025-01-07}'
  ```
  BigQuery rejects correlated subqueries on another table, so domain matching is a cross join with the seed plus `qualify`.
  - Pitfalls: dbt renders a disabled model's SQL at parse time, so the adapter's validation raises only when `execute` (`_mmm_ready_ga4_fail`) and returns placeholders otherwise, or a default build would fail on unset GA4 vars. Package macros called from another project must call sibling macros as `mmm_ready.<name>()`: unqualified calls do not resolve there.
- **Holidays** (`fct_outcomes_daily`): filters the `holidays` seed by `mmm_ready_holiday_country` (default `ES`). For any other country `holiday_flag` stays 0 unless rows for that country are added to `seeds/holidays.csv`.
- **Channel mapping** (`int_mmm__spend_mapped`): left-joins spend on `(platform, campaign_type)` to the `channel_taxonomy` seed; anything unmatched becomes `mmm_channel = 'unmapped'`. Everything downstream keys off `mmm_channel`, and `unmapped` is a deliberate sentinel tracked by the diagnostics.
- **Taxonomy conventions** (`seeds/channel_taxonomy.csv`, full channel table in the README):
  - `mmm_channel` is `snake_case`, `family_qualifier` when a family has several variants (`search_brand`, `social_prospecting`, `video_online`); a channel that is its own family or has an established industry name keeps a single name (`tv`, `ctv`, `radio`, `ooh`, `affiliate`, `retail_media`, `performance_max`).
  - `channel_group` is the family: `search`, `social`, `video`, `display`, `retail_media`, `audio`, `ooh`, `affiliate`, `ai`.
  - `ai_assistant` is only for ads bought directly on AI assistants (openai, perplexity). Google ads in AI Overviews / AI Mode run inside Google Ads campaigns and can't be split out, so they stay in the search channels and `performance_max`; don't map them to `ai_assistant`.
  - `demand_capture` (boolean, type pinned in the root `dbt_project.yml`) is `true` for channels that mainly harvest existing demand: `search_brand`, `social_retargeting`, `retail_media`, `affiliate`. It flows through `int_mmm__spend_mapped` to `fct_marketing_spend_daily` and is `null` for `unmapped`.
  - `(platform, campaign_type)` must be unique, or the join fans out and duplicates spend.
  - Renaming a channel renames `mmm_input_weekly` columns: treat it as a breaking change and bump the package version.
  - Pinterest must stay out of the taxonomy (seeded unmapped issue).
- **Dynamic pivot** (`mmm_input_weekly`): column list comes from `macros/get_mmm_channels.sql`, which runs `run_query` at execute time. By default it returns channels with `sum(spend) > 0` in `int_mmm__spend_weekly` (excluding `unmapped`); with `mmm_ready_pivot_all_channels: true`, or when that view does not exist yet (checked with `adapter.get_relation`), it falls back to every channel in the taxonomy seed. This is why the model has a `-- depends_on: {{ ref('channel_taxonomy') }}` hint. `spend_unmapped` is always present. At parse time (`execute` false) the macro returns `[]`, so output from `dbt compile`/`dbt ls` before the seeds are loaded shows no per-channel columns — expected, not a bug.
- **Partial weeks** (`mmm_input_weekly`): with `mmm_ready_drop_partial_weeks: true` (default) the first and last week are dropped if they have fewer than 7 distinct days in `fct_outcomes_daily`, counted across the whole panel (not per geo). Interior gaps are not dropped. The readiness report never reads `mmm_input_weekly`, so it always sees the full data (it handles partial edge weeks on its own, see Diagnostics).
- **Boolean vars**: read them with `mmm_ready.mmm_ready_as_bool(var(...))`, never a bare `var(...)` in an `if`: values passed with `--vars` can arrive as strings, and `"false"` is truthy in Jinja.
- **Diagnostics**: `mmm_channel_correlations` computes pairwise weekly spend correlation (excluding `unmapped`); `mmm_readiness_report` consumes it plus `int_mmm__spend_weekly`, `int_mmm__platform_weekly` (per-platform gap detection) and `fct_outcomes_daily`. Flags split into blocking (`flag_unmapped_spend`, `flag_short_history`, `flag_kpi_gaps`) and review (`flag_platform_gaps`, `flag_sparse`, `flag_low_variance`, `flag_spike`, `flag_collinear`); status is derived from those sums. Per-week statistics (sparsity, variance, spikes, and the correlations) run over complete weeks only: `int_mmm__spend_week_calendar` marks a first/last week with fewer than 7 days of spend data as partial, independently of `mmm_ready_drop_partial_weeks`; history length, KPI coverage, spend shares and gaps still use the full period. All thresholds are `mmm_ready_*` vars in the root `dbt_project.yml`.
- **Week boundaries**: always `{{ mmm_ready.mmm_ready_week_start('date_day') }}` (`macros/mmm_ready_week_start.sql`), never `dbt.date_trunc('week', ...)`, whose week start is warehouse-dependent (DuckDB Monday, BigQuery Sunday). The macro computes `date - mod(days since an anchor, 7)` with `dbt.datediff`/`dbt.dateadd`; the weekday comes from `mmm_ready_week_start` (default `monday`), so every warehouse returns identical weeks. DuckDB (CI) and BigQuery (manual) are validated: on the synthetic data the readiness report matches bit for bit in every flag, status and count, and float columns agree to ~1e-15 (summation order differs, so floats are never bit-identical; compare them with a tolerance); Databricks/Snowflake not yet.
- **BigQuery SQL pitfalls** already hit: a CTE must not share a name with one of its columns (BigQuery resolves the bare name to the row STRUCT), and `WHERE` needs a `FROM` (use `from (select 1 as x) t`).

## Tests

- Generic test `mmm_ready.unique_combination` (`macros/test_unique_combination.sql`) is used in `models/schema.yml` with the `arguments:` block syntax.
- `tests/assert_unmapped_spend_share.sql` is a singular test at `severity: warn`.
- Package singular tests `tests/assert_no_partial_edge_weeks.sql` and `tests/assert_no_all_zero_spend_columns.sql` check the `mmm_input_weekly` cleanup invariants on any warehouse; each returns no rows when its var is set to the non-default value.
- `integration_tests/tests/assert_mmm_input_weekly_shape.sql` checks exact values on the synthetic data (week range, `spend_ctv` presence) on any warehouse; it derives the expected edge weeks in Jinja date arithmetic from `mmm_ready_week_start`, independently of the macro. Package vars are not visible from the `integration_tests` project unless passed with `--vars`, so tests there must call `var(name, default)` with the same default as the root `dbt_project.yml`.

## Synthetic data and seeded issues

`scripts/generate_synthetic_data.py` writes the three raw CSVs into `integration_tests/seeds/` and `integration_tests/ground_truth.json` (both gitignored). It deliberately plants issues the readiness report is expected to catch: a TikTok gap 2024-09-02..2024-09-22, a ×10 Meta prospecting spike the week of 2024-06-03, YouTube collinear with TV, and Pinterest missing from the taxonomy (→ unmapped spend). `ground_truth.json` also stores true per-channel ROI for validating a downstream MMM. Under `response_curves` it stores, for each `mmm_channel` (aggregated through `seeds/channel_taxonomy.csv`): true incremental revenue at 50–150% of historical spend, a dense 0–2× curve, mROI at +1%, and weekly adstock weights. These are computed deterministically after all random draws, so they must never change the generated CSVs. Check that by hashing the CSVs before and after a generator change. In the counterfactuals, each channel's Hill `scale` stays at its historical value. `examples/meridian/` (not run in CI) compares a Meridian fit against all of this. Changes to diagnostic logic must also keep these issues flagged, and the taxonomy seed must stay consistent with the generator (e.g. Pinterest must remain unmapped).

## Seed column types

Seed column types are pinned explicitly: package seeds in the root `dbt_project.yml` (`channel_taxonomy`, `holidays`), and the generated CSVs in `integration_tests/dbt_project.yml` (`date: date`). A new seed or date column needs the same pinning.
