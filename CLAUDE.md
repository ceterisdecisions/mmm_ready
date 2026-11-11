# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`mmm_ready` is a dbt **package** (not a standalone project) that turns raw marketing data into a wide weekly table ready for Marketing Mix Modeling (Meridian, PyMC-Marketing, Robyn), plus a data-quality diagnostic (`mmm_readiness_report`) that labels each channel `ready` / `review` / `blocked`. The whole repo is in English: docs, code comments, model/source descriptions, log messages and commit messages — keep that convention. (Seed data values such as Spanish holiday names are data, not prose, and stay as-is.)

The root has no `profiles.yml`/profile; it's consumed by other projects. Development and CI run through `integration_tests/`, a separate dbt project that installs the package via `packages.yml` (`local: ../`) and runs on DuckDB.

## Project rules

- Warehouse-agnostic dbt package: use only dbt cross-database macros (`dbt.date_trunc`, `dbt.datediff`, `dbt.listagg`, `dbt.type_*`...) and standard SQL.
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

CI (`.github/workflows/ci.yml`) runs exactly: generate data → `dbt deps` → `dbt seed` → `dbt build` → `dbt build --vars '{mmm_ready_drop_partial_weeks: false, mmm_ready_pivot_all_channels: true}'` on Python 3.11. A new boolean var should be added to that second build so CI covers its non-default branch.

## Architecture

Layered pipeline (`models/`): `staging` (views) → `intermediate` (views) → `marts` (tables) → `diagnostics` (tables).

- **Sources** (`models/staging/_sources.yml`): three raw tables (`ad_spend_daily`, `outcomes_daily`, `controls_daily`) whose database/schema/identifier are all overridable via `mmm_ready_raw_*` vars. In integration tests these are the generated seeds. Staging lowercases/trims `geo`, `platform`, `campaign_type` and casts with cross-db `dbt.type_*()` macros. Required raw columns (selected by name, so a mismatch breaks the build):
  - `ad_spend_daily`: date, geo, platform, campaign_type, spend, impressions, clicks
  - `outcomes_daily`: date, geo, revenue, conversions
  - `controls_daily`: date, geo, avg_price, promo_flag
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
- **Partial weeks** (`mmm_input_weekly`): with `mmm_ready_drop_partial_weeks: true` (default) the first and last week are dropped if they have fewer than 7 distinct days in `fct_outcomes_daily`, counted across the whole panel (not per geo). Interior gaps are not dropped. The readiness report never reads `mmm_input_weekly`, so it always sees the full data.
- **Boolean vars**: read them with `mmm_ready.mmm_ready_as_bool(var(...))`, never a bare `var(...)` in an `if`: values passed with `--vars` can arrive as strings, and `"false"` is truthy in Jinja.
- **Diagnostics**: `mmm_channel_correlations` computes pairwise weekly spend correlation (excluding `unmapped`); `mmm_readiness_report` consumes it plus `int_mmm__spend_weekly`, `int_mmm__platform_weekly` (per-platform gap detection) and `fct_outcomes_daily`. Flags split into blocking (`flag_unmapped_spend`, `flag_short_history`, `flag_kpi_gaps`) and review (`flag_platform_gaps`, `flag_sparse`, `flag_low_variance`, `flag_spike`, `flag_collinear`); status is derived from those sums. All thresholds are `mmm_ready_*` vars in the root `dbt_project.yml`.
- **Week boundaries** use `dbt.date_trunc('week', ...)`, so week start is warehouse-dependent (e.g. BigQuery = Sunday). Only DuckDB is tested; Databricks/Snowflake not yet validated.

## Tests

- Generic test `mmm_ready.unique_combination` (`macros/test_unique_combination.sql`) is used in `models/schema.yml` with the `arguments:` block syntax.
- `tests/assert_unmapped_spend_share.sql` is a singular test at `severity: warn`.
- Package singular tests `tests/assert_no_partial_edge_weeks.sql` and `tests/assert_no_all_zero_spend_columns.sql` check the `mmm_input_weekly` cleanup invariants on any warehouse; each returns no rows when its var is set to the non-default value.
- `integration_tests/tests/assert_mmm_input_weekly_shape.sql` checks exact values on the synthetic data (week range, `spend_ctv` presence) and is DuckDB-specific. Package vars are not visible from the `integration_tests` project unless passed with `--vars`, so tests there must call `var(name, default)` with the same default as the root `dbt_project.yml`.

## Synthetic data and seeded issues

`scripts/generate_synthetic_data.py` writes the three raw CSVs into `integration_tests/seeds/` and `integration_tests/ground_truth.json` (both gitignored). It deliberately plants issues the readiness report is expected to catch: a TikTok gap 2024-09-02..2024-09-22, a ×10 Meta prospecting spike the week of 2024-06-03, YouTube collinear with TV, and Pinterest missing from the taxonomy (→ unmapped spend). `ground_truth.json` also stores true per-channel ROI for validating a downstream MMM. Changes to diagnostic logic must also keep these issues flagged, and the taxonomy seed must stay consistent with the generator (e.g. Pinterest must remain unmapped).

## Seed column types

Seed column types are pinned explicitly: package seeds in the root `dbt_project.yml` (`channel_taxonomy`, `holidays`), and the generated CSVs in `integration_tests/dbt_project.yml` (`date: date`). A new seed or date column needs the same pinning.
