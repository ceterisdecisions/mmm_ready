"""Spend and controls that line up with the public GA4 / Firebase samples, for the BigQuery runs.

The public exports have no marketing spend, and the synthetic generator's spend covers 2023-2025
in Spanish regions, so a run with it has no week where spend and KPI overlap (every spend column
of mmm_input_weekly is zero). This writes <window>_ad_spend_daily.csv and
<window>_controls_daily.csv for the sample's dates and the countries used in the README's
commands (geo = lowercased geo.country):
- ga4:      bigquery-public-data.ga4_obfuscated_sample_ecommerce, 2020-11-01..2021-01-31
- firebase: firebase-public-project.analytics_153293282, 2018-06-12..2018-10-03

Plain random spend: it has no planted effect on the KPI and no seeded issues; it only exercises
the pipeline end to end. Separate from generate_synthetic_data.py so that script's outputs and
ground_truth.json are untouched.
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

# window -> (start, end, {geo: relative size}); countries with most conversions in each sample
WINDOWS = {
    "ga4": ("2020-11-01", "2021-01-31", {"united states": 1.0, "canada": 0.25, "india": 0.2}),
    "firebase": ("2018-06-12", "2018-10-03", {"united states": 1.0, "japan": 0.15, "australia": 0.05}),
}
# (platform, campaign_type) -> base daily spend; all mapped in seeds/channel_taxonomy.csv
CAMPAIGNS = {
    ("google_ads", "search_brand"): 300,
    ("google_ads", "search_nonbrand"): 500,
    ("meta", "prospecting"): 400,
    ("meta", "retargeting"): 150,
    ("youtube", "awareness"): 250,
}
CPM = {"google_ads": 8, "meta": 6, "youtube": 9}
CTR = {"google_ads": 0.04, "meta": 0.01, "youtube": 0.003}


def main(out_dir: Path, seed: int, window: str) -> None:
    rng = np.random.default_rng(seed)
    start, end, geos = WINDOWS[window]
    dates = pd.date_range(start, end, freq="D")
    spend_rows, control_rows = [], []
    for geo, size in geos.items():
        for (platform, ctype), base in CAMPAIGNS.items():
            spend = base * size * rng.lognormal(0, 0.3, len(dates))
            impressions = spend / CPM[platform] * 1000
            spend_rows.append(pd.DataFrame({
                "date": dates.strftime("%Y-%m-%d"), "geo": geo, "platform": platform,
                "campaign_type": ctype, "spend": spend.round(2),
                "impressions": impressions.round().astype(int),
                "clicks": (impressions * CTR[platform]).round().astype(int),
            }))
        control_rows.append(pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"), "geo": geo,
            "avg_price": (50 * rng.lognormal(0, 0.02, len(dates))).round(2),
            "promo_flag": (rng.random(len(dates)) < 0.1).astype(int),
        }))
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.concat(spend_rows).to_csv(out_dir / f"{window}_ad_spend_daily.csv", index=False)
    pd.concat(control_rows).to_csv(out_dir / f"{window}_controls_daily.csv", index=False)
    print(f"OK -> {out_dir}  ({sum(len(d) for d in spend_rows)} spend rows)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="integration_tests/seeds")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--window", choices=sorted(WINDOWS), default="ga4")
    a = ap.parse_args()
    main(Path(a.out), a.seed, a.window)
