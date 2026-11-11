"""
Generates synthetic marketing data with "known truth" for mmm_ready.

- 3 years of daily data (2023-2025), 3 geos in Spain.
- Each channel has geometric adstock + Hill saturation + beta defined here.
- Writes ground_truth.json with each channel's true ROI, to validate the MMM in month 2.
- Deliberately seeds issues for the readiness report to detect:
    1) 3-week gap with no TikTok rows across all geos
    2) x10 spend spike in Meta prospecting for one week
    3) YouTube nearly collinear with TV
    4) Pinterest unmapped in the taxonomy

Usage:  python scripts/generate_synthetic_data.py [--out integration_tests/seeds] [--seed 42]
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

GEOS = {"es-md": 1.0, "es-ct": 0.9, "es-an": 0.7}  # relative size

# (platform, campaign_type) -> spend and effect parameters
CAMPAIGNS = {
    ("google_ads", "search_brand"):    dict(base=900,  season=0.3, flight=None,  adstock=0.1, k=0.9, s=1.2, beta=2600),
    ("google_ads", "search_nonbrand"): dict(base=1400, season=0.4, flight=None,  adstock=0.2, k=1.0, s=1.5, beta=2900),
    ("meta", "prospecting"):           dict(base=1100, season=0.3, flight=None,  adstock=0.4, k=1.1, s=1.8, beta=2100),
    ("meta", "retargeting"):           dict(base=400,  season=0.2, flight=None,  adstock=0.2, k=0.8, s=1.3, beta=700),
    ("tiktok", "prospecting"):         dict(base=500,  season=0.3, flight=None,  adstock=0.3, k=1.0, s=2.0, beta=650),
    ("tv", "linear"):                  dict(base=4000, season=0.5, flight=(3, 2), adstock=0.7, k=1.2, s=2.2, beta=5200),
    ("pinterest", "prospecting"):      dict(base=700,  season=0.2, flight=None,  adstock=0.3, k=1.0, s=1.5, beta=150),
}
# youtube is generated as a function of TV (seeded collinearity)
YOUTUBE = dict(adstock=0.5, k=1.0, s=1.8, beta=1200)
CPM = {"google_ads": 8, "meta": 6, "tiktok": 5, "tv": 12, "youtube": 9, "pinterest": 4}
CTR = {"google_ads": 0.04, "meta": 0.01, "tiktok": 0.008, "tv": 0.0, "youtube": 0.003, "pinterest": 0.006}


def geometric_adstock(x, decay):
    out = np.zeros_like(x)
    carry = 0.0
    for i, v in enumerate(x):
        carry = v + decay * carry
        out[i] = carry
    return out


def hill(x, k, s):
    x = np.maximum(x, 0)
    return x**s / (x**s + k**s)


def flighting(dates, on_weeks, off_weeks):
    week_idx = ((dates - dates[0]).days // 7).to_numpy()
    return (week_idx % (on_weeks + off_weeks) < on_weeks).astype(float)


def main(out_dir: Path, seed: int):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-01", "2025-12-31", freq="D")
    n = len(dates)
    doy = dates.dayofyear.to_numpy()
    season = 1 + 0.15 * np.sin(2 * np.pi * (doy - 80) / 365) + 0.25 * np.exp(-((doy - 335) ** 2) / 200)
    trend = np.linspace(1.0, 1.12, n)
    holidays = set(pd.read_csv(Path(__file__).resolve().parents[1] / "seeds" / "holidays.csv")["holiday_date"])
    holiday = np.array([d.strftime("%Y-%m-%d") in holidays for d in dates], dtype=float)

    spend_rows, outcome_rows, control_rows = [], [], []
    truth = {}

    for geo, size in GEOS.items():
        contrib_total = np.zeros(n)
        tv_spend = None
        channel_spend = {}

        for (platform, ctype), p in CAMPAIGNS.items():
            spend = p["base"] * size * (1 + p["season"] * (season - 1) / 0.4)
            if p["flight"]:
                spend = spend * flighting(dates, *p["flight"])
            spend = spend * rng.lognormal(0, 0.25, n)
            if (platform, ctype) == ("meta", "prospecting"):          # issue 2: x10 spike
                spike = (dates >= "2024-06-03") & (dates <= "2024-06-09")
                spend = np.where(spike, spend * 10, spend)
            channel_spend[(platform, ctype)] = spend
            if platform == "tv":
                tv_spend = spend

        # YouTube collinear with TV (issue 3)
        yt = 0.3 * tv_spend * rng.lognormal(0, 0.08, n)
        channel_spend[("youtube", "awareness")] = yt

        for (platform, ctype), spend in channel_spend.items():
            p = YOUTUBE if platform == "youtube" else CAMPAIGNS[(platform, ctype)]
            ad = geometric_adstock(spend, p["adstock"])
            scale = ad[ad > 0].mean()
            effect = p["beta"] * size * hill(ad / scale, p["k"], p["s"])
            contrib_total += effect
            key = f"{platform}/{ctype}"
            t = truth.setdefault(key, {"adstock_decay": p["adstock"], "hill_k": p["k"], "hill_s": p["s"],
                                       "spend": 0.0, "contribution": 0.0})
            t["spend"] += float(spend.sum())
            t["contribution"] += float(effect.sum())

            impressions = (spend / CPM[platform] * 1000 * rng.lognormal(0, 0.1, n)).round()
            clicks = (impressions * CTR[platform] * rng.lognormal(0, 0.15, n)).round()
            df = pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "geo": geo, "platform": platform,
                               "campaign_type": ctype, "spend": spend.round(2),
                               "impressions": impressions.astype(int), "clicks": clicks.astype(int)})
            df = df[df["spend"] > 0]
            if platform == "tiktok":                                   # issue 1: 3-week gap
                gap = (pd.to_datetime(df["date"]) >= "2024-09-02") & (pd.to_datetime(df["date"]) <= "2024-09-22")
                df = df[~gap]
            spend_rows.append(df)

        price = 50 * (1 + 0.03 * np.sin(2 * np.pi * doy / 365)) * rng.normal(1, 0.01, n)
        promo = (rng.random(n) < 0.08).astype(int)
        base = 22000 * size * season * trend
        revenue = (base * (1 + 0.12 * promo) * (1 - 0.05 * holiday) * (50 / price) ** 1.2
                   + contrib_total) * rng.normal(1, 0.04, n)
        outcome_rows.append(pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "geo": geo,
                                          "revenue": revenue.round(2), "conversions": (revenue / price).round().astype(int)}))
        control_rows.append(pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "geo": geo,
                                          "avg_price": price.round(2), "promo_flag": promo}))

    for t in truth.values():
        t["roi"] = round(t["contribution"] / t["spend"], 3)
        t["spend"] = round(t["spend"], 2)
        t["contribution"] = round(t["contribution"], 2)

    out_dir.mkdir(parents=True, exist_ok=True)
    pd.concat(spend_rows).to_csv(out_dir / "ad_spend_daily.csv", index=False)
    pd.concat(outcome_rows).to_csv(out_dir / "outcomes_daily.csv", index=False)
    pd.concat(control_rows).to_csv(out_dir / "controls_daily.csv", index=False)
    (out_dir.parent / "ground_truth.json").write_text(json.dumps(
        {"seed": seed, "channels": truth,
         "seeded_issues": ["tiktok gap 2024-09-02..2024-09-22", "meta prospecting x10 spike week of 2024-06-03",
                           "youtube collinear with tv", "pinterest unmapped in taxonomy"]},
        indent=2, ensure_ascii=False))
    print(f"OK -> {out_dir}  ({sum(len(d) for d in spend_rows)} spend rows)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="integration_tests/seeds")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    main(Path(a.out), a.seed)
