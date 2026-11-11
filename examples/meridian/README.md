# Meridian smoke test

An end-to-end check that `mmm_input_weekly` can be loaded straight into
[Meridian](https://developers.google.com/meridian). It fits a geo-level model on the
synthetic data and compares the estimated ROI per `mmm_channel` against
`ground_truth.json`.

This is an example, not a test, so CI does not run it.

## What the script does

`run_meridian.py`:

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
   `output/roi_comparison.csv` (gitignored).

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

# Full run (much slower on CPU; a GPU is recommended)
.venv-meridian/bin/python examples/meridian/run_meridian.py --full
```

On macOS with the python.org installer, if `pip install` fails with
`CERTIFICATE_VERIFY_FAILED`, run `Install Certificates.command` from the Python 3.12
folder in Applications. Alternatively, set `SSL_CERT_FILE` to the path printed by
`python -c "import pip._vendor.certifi as c; print(c.where())"`.

## Run on Google Colab (GPU)

Choose **Runtime → Change runtime type → GPU** (a T4 is enough), then run these cells:

```python
!git clone <your-fork-url> mmm_ready
%cd mmm_ready
!pip install -q dbt-duckdb pandas numpy
```

```python
%cd integration_tests
!python ../scripts/generate_synthetic_data.py --out seeds
!DBT_PROFILES_DIR=. dbt deps && DBT_PROFILES_DIR=. dbt seed && DBT_PROFILES_DIR=. dbt build
%cd ..
```

```python
!pip install -q "google-meridian[and-cuda]==2.2.0" duckdb
import jax; print(jax.devices())   # should list a CUDA device
```

```python
!python examples/meridian/run_meridian.py --full
```

If pip reports dependency conflicts with packages that come preinstalled in Colab, restart
the runtime after the Meridian install (**Runtime → Restart session**) and run the last two
cells again.

## Reading the results

A quick run is a check that the pipeline works, not an estimate. Expect R-hat well above
1.2 and wide intervals. Even with `--full`, the problems planted in the synthetic data
distort the comparison in predictable ways, and that is the point of the example:

- **`tv` vs `video_online`:** YouTube spend is generated as a function of TV
  (`flag_collinear` in the readiness report). The model can't split credit between them, so
  expect wide or swapped ROIs for these two channels.
- **`unmapped`:** Pinterest is missing from the taxonomy. Its spend is excluded and its
  (small) true effect is absorbed by the baseline. It appears in the table with a true ROI
  but no estimate.
- **`social_prospecting`:** the ×10 Meta spike in the week of 2024-06-03 and the 3-week
  TikTok gap in September 2024 add noise to this channel.
- **Window mismatch:** the true ROI is computed over the full generated period, but the model
  only sees the weeks kept in `mmm_input_weekly`, which drops partial edge weeks.
- **Default priors:** the example uses Meridian's default ROI priors and has no calibration.
  If an interval misses the truth, read that as a hint, not a failure.
