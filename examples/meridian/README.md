# Meridian smoke test

An end-to-end check that `mmm_input_weekly` can be loaded straight into
[Meridian](https://developers.google.com/meridian). It fits a geo-level model on the
synthetic data and compares what Meridian estimates with `ground_truth.json`, by
`mmm_channel`. Each step is a separate script:

| Script | What it does | Output (`output/`, gitignored) |
|---|---|---|
| `run_meridian.py` | Fits the model, compares ROI, computes convergence diagnostics, saves the fit | `model.binpb`, `roi_comparison.csv`, `convergence.csv`, `run_config.json` |
| `response_curves.py` | Compares response curves, saturation, adstock and marginal ROI | `plots/*.png`, `response_curves_comparison.csv`, `mroi_comparison.csv`, `adstock_comparison.csv` |
| `optimize_budget.py` | Runs Meridian's budget optimizer and scores it against the true optimum | `plots/budget_allocation.png`, `budget_allocation_comparison.csv`, `budget_summary.csv` |
| `validate.py` | Collects the above into one report: convergence verdict first, then every check against the truth | `validation_report.md`, `validation_report.csv` |
| `colab_full_run.ipynb` | Runs everything with `--full` on Google Colab | the same files, downloaded as a zip |

The later scripts load `model.binpb`, so only `run_meridian.py` pays the sampling cost.

This is an example, not a test, so CI does not run it.

## Fitting and ROI: `run_meridian.py`

1. Reads `mmm_input_weekly` from `integration_tests/mmm_ready_dev.duckdb` (opened read-only).
2. Loads it into Meridian at geo level:
   - KPI: `revenue` (`kpi_type="revenue"`).
   - Media: `impressions_<channel>` as exposure and `spend_<channel>` as media spend. The
     channels are detected from the `spend_*` columns. `spend_unmapped` is left out.
   - Controls: `avg_price`, `promo_days`, `holiday_days`.
   - Population: a constant 1 per geo, because the synthetic panel has no population data.
3. Samples the prior and the posterior. The default quick preset finishes in a few minutes on CPU.
4. Builds the true ROI per `mmm_channel`. It maps each `platform/campaign_type` key in
   `ground_truth.json` through `seeds/channel_taxonomy.csv`, then computes
   `sum(contribution) / sum(spend)` per channel. For example, `social_prospecting` combines
   Meta and TikTok.
5. Prints the true ROI next to the posterior mean and the 90% credible interval, then writes
   `output/roi_comparison.csv`.
6. Saves the fitted model to `output/model.binpb` with `save_meridian`.
7. Computes the convergence diagnostics and prints the verdict (see
   [Convergence and validation](#convergence-and-validation)).

It stops before fitting if any `spend_*` or `impressions_*` column is all zeros. That happens
when `mmm_input_weekly` was last built with `mmm_ready_pivot_all_channels: true` (CI's second
build does this): rebuild with the default vars first.

### Options

| Flag | Default | Notes |
|---|---|---|
| `--full` | off | Full preset: 4 chains, 500 adapt, 500 burn-in, 1000 kept draws, 500 prior draws |
| `--chains`, `--n-adapt`, `--n-burnin`, `--n-keep`, `--n-prior` | quick preset: 2 / 200 / 200 / 200 / 200 | Override any single sampling setting |
| `--knots` | 39 | Baseline knots, about one every 4 weeks. Must stay below the number of weeks (see below) |
| `--seed` | 42 | |
| `--db`, `--table`, `--ground-truth`, `--taxonomy`, `--out` | repo paths | Use these to point at other data |

**Why `--knots` is set:** Meridian's geo model uses one knot per week by default. A
control that is identical in every geo, such as `holiday_days` here, is then perfectly
collinear with the time effects, and Meridian refuses to build the model. Using fewer knots
keeps the holiday control identifiable.

## Curves, saturation, adstock and mROI: `response_curves.py`

The truth comes from `ground_truth.json` → `response_curves`, which the generator writes per
`mmm_channel`. Both sides use Meridian's definition of a response curve: scale the channel's
spend by a multiplier on every day and geo, keep the flighting pattern, and sum the incremental
revenue. The x-axis is always the multiplier (1 = historical spend). That keeps the two curves
aligned even though Meridian only sees the weeks kept in `mmm_input_weekly`.

| Plot (`output/plots/`) | Meridian | Truth |
|---|---|---|
| `response_curves.png` | `Analyzer.response_curves()`, mean and 90% CI | dense curve, 0–2× |
| `saturation_normalized.png` | same curves divided by their value at 100%, to compare shape rather than level | same |
| `mroi.png` | `Analyzer.marginal_roi(incremental_increase=0.01)` | forward difference at +1% |
| `adstock.png` | `Analyzer.adstock_decay()`, weight αˡ by weekly lag | daily geometric decay converted to weekly lags, scaled so lag 0 = 1 |
| `hill_curves.png` | `Analyzer.hill_curves()` with the histogram of historical media | not shown (see below) |

The console also prints incremental revenue at 50/75/100/125/150% of spend and the mROI
table with interval coverage.

**Why Hill parameters aren't compared directly:** Meridian applies Hill to impressions per
geo-week, scaled by its own median. The generator applies it to daily adstocked spend
divided by the historical mean. The two `k`/`s` pairs live on different axes, so saturation
is compared by curve shape (`saturation_normalized.png`) instead.
The adstock comparison is likewise an approximation, because the truth saturates daily and
Meridian weekly.

## Budget optimizer: `optimize_budget.py`

1. **Meridian:** `BudgetOptimizer.optimize(fixed_budget=True)` keeps the historical total
   budget and holds each channel within ±30% of its historical spend, which is Meridian's
   default. Change the range with `--spend-bounds` (for example `0.5` for ±50%).
2. **True optimum:** the same problem (same budget, same bounds) solved on the true curves.
   It uses PCHIP interpolation of the 0–2× grid and SLSQP from several starting points
   (`--starts`). At the optimum, every channel strictly inside its bounds has the same true
   marginal ROI.
3. **Scoring:** the historical allocation, Meridian's recommendation and the true optimum are
   all evaluated on the **true** curves:

   ```
   captured = (R_meridian − R_historical) / (R_true_optimum − R_historical)
   ```

   100% means Meridian found the true optimum, 0% means no gain, and a negative value means
   its recommendation loses revenue compared with doing nothing. The summary also prints
   the uplift Meridian *predicted* for its own recommendation, next to the real one.

Allocations are compared as multipliers of historical spend. Unmapped spend (Pinterest) is
outside the model and stays fixed.

## Convergence and validation

This section describes the method only. A result is one run (seed 42) on synthetic data, and
every threshold below is a **heuristic**: the convergence thresholds follow Vehtari et al.
(2021) and the validation rules are a choice of this example. Neither is validated on this model.

### Convergence criterion

`run_meridian.py` computes, for every posterior parameter, the worst value over its elements of:

| Diagnostic | Source | Threshold |
|---|---|---|
| R-hat, rank-normalized split | `arviz.rhat(method="rank")` | ≤ 1.01 |
| ESS bulk and tail, over all chains | `arviz.ess(method="bulk" / "tail")` | ≥ 400 |
| Divergent transitions (kept draws) | `inference_data.sample_stats.diverging` | 0 |
| R-hat, classic | Meridian's `Analyzer.get_rhat()` | < 1.2 (Meridian's default, reported only) |

Parameters that are constant across draws (for example `slope_m`, because Meridian fixes the Hill
slope at 1 by default) have no R-hat and are left out. The verdict is:

- **converged**: every parameter meets the first three rows;
- **acceptable**: it doesn't, but the classic R-hat is below Meridian's 1.2;
- **not converged**: anything else.

Only **converged** counts as converged: under the other two verdicts the ROI, mROI and budget
numbers are not reliable estimates. The diagnostics are reported only. The scripts never resample
or change priors, chains, knots or data based on them. `convergence.csv` lists every parameter,
and `run_config.json` stores the sampling settings, seed, library versions, JAX devices,
sampling time and the verdict.

### Validation against the truth

`validate.py` writes `validation_report.md`, whose first line is the convergence verdict, followed
by a warning when the run didn't converge. The scored checks are:

| Check | Passes when |
|---|---|
| ROI per channel | the true ROI is inside the 90% credible interval |
| Marginal ROI per channel (+1%) | the true mROI is inside the 90% credible interval |
| Budget optimizer | Meridian's allocation captures a positive share of the possible improvement |

The report also shows, without scoring them, each channel's relative ROI error and rank, the
Spearman rank correlation of true vs estimated ROI, and whether Meridian moves each channel's
budget in the same direction as the true optimum. Channels touched by the planted issues
(`tv`, `video_online`, `social_prospecting`, `unmapped`) are labeled, but their checks count
like any other, so every failure stays visible. `unmapped` is not estimated, so it isn't scored.

## Run locally (CPU)

Meridian gets its own virtual environment so its dependencies (JAX, TensorFlow,
TFP nightly) don't clash with dbt's. Both environments use Python 3.12. Run these
commands from the repo root.

```bash
# 1. Build the data with dbt (dev environment)
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cd integration_tests && export DBT_PROFILES_DIR=.
python ../scripts/generate_synthetic_data.py --out seeds
dbt deps && dbt seed && dbt build
deactivate && cd ..

# 2. Meridian environment
python3.12 -m venv .venv-meridian
.venv-meridian/bin/pip install -r examples/meridian/requirements.txt

# 3. Quick smoke run (about 1-2 minutes on a laptop CPU)
.venv-meridian/bin/python examples/meridian/run_meridian.py
#    or a full run (much slower on CPU; a GPU is recommended)
.venv-meridian/bin/python examples/meridian/run_meridian.py --full

# 4. Curves, budget optimizer and validation report on the saved model (seconds each)
.venv-meridian/bin/python examples/meridian/response_curves.py
.venv-meridian/bin/python examples/meridian/optimize_budget.py
.venv-meridian/bin/python examples/meridian/validate.py
```

On macOS with the python.org installer, if `pip install` fails with
`CERTIFICATE_VERIFY_FAILED`, run `Install Certificates.command` from the Python 3.12
folder in Applications. Alternatively, set `SSL_CERT_FILE` to the path printed by
`python -c "import pip._vendor.certifi as c; print(c.where())"`.

## Run on Google Colab

Open [`colab_full_run.ipynb`](colab_full_run.ipynb) in Colab (**File → Open notebook → GitHub**,
then paste the repo URL `https://github.com/ceterisdecisions/mmm_ready`). A GPU runtime
(**Runtime → Change runtime type → GPU**, a T4 is enough) is recommended but not required.
The notebook:

1. clones `https://github.com/ceterisdecisions/mmm_ready.git` at `BRANCH` (default `main`),
2. generates the synthetic data and runs `dbt build` with the default vars,
3. installs `google-meridian[and-cuda,schema]==2.2.0`,
4. runs a quick fit as a timing reference (into `output_quick/`, not validated),
5. runs `run_meridian.py --full`, `response_curves.py`, `optimize_budget.py` and `validate.py`,
6. shows the report and plots and downloads `output/` as a zip.

It needs no credentials: the repo is public and the data is generated in the runtime. If pip
reports dependency conflicts with Colab's preinstalled packages, restart the session
(**Runtime → Restart session**) after the Meridian install and continue from the next cell.
The full run's duration hasn't been measured yet; use the quick run's sampling time
(4 chains × 2000 steps vs 2 chains × 600) as a rough guide.

## Reading the results

A quick run is a check that the pipeline works, not an estimate. Expect R-hat well above
1.2 and wide intervals. Even with `--full`, the problems planted in the synthetic data
distort the comparison in predictable ways, and that is the point of the example:

- **`tv` vs `video_online`:** YouTube spend is generated as a function of TV
  (`flag_collinear` in the readiness report). The model can't split credit between them, so
  expect wide or swapped ROIs, curves and mROIs for these two channels. Typically
  `video_online` comes out too high and `tv` too low.
- **S-shaped truth:** TV has the steepest true Hill curve (`s = 2.2`), so its true response is
  S-shaped at low multipliers. Meridian's default prior favours concave curves.
- **`unmapped`:** Pinterest is missing from the taxonomy. Its spend is excluded and its
  (small) true effect is absorbed by the baseline. It appears in the table with a true ROI
  but no estimate.
- **`social_prospecting`:** the ×10 Meta spike in the week of 2024-06-03 and the 3-week
  TikTok gap in September 2024 add noise to this channel.
- **Optimizer:** with these true curves, the possible gain within ±30% is small (about 1%
  of incremental revenue), because the true marginal ROIs at historical spend are already
  close to each other. Any bias in the fitted curves is therefore enough to push the
  recommendation the wrong way. In particular, the TV/YouTube collinearity makes Meridian
  move budget from `tv` to `video_online`, the opposite of the true optimum. A quick run
  usually captures a negative share; judge the optimizer only on a converged `--full` run.
- **Window mismatch:** the true ROI is computed over the full generated period, but the model
  only sees the weeks kept in `mmm_input_weekly`, which drops partial edge weeks.
- **Default priors:** the example uses Meridian's default ROI priors and has no calibration.
  If an interval misses the truth, read that as a hint, not a failure.
