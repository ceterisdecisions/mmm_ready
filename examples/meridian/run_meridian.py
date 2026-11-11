"""
Meridian smoke test on the mmm_ready synthetic data.

Reads mmm_input_weekly from the integration_tests DuckDB database, fits a geo-level
Meridian model (revenue KPI, impressions_* as media, spend_* as media spend,
avg_price / promo_days / holiday_days as controls) and compares the estimated ROI
per mmm_channel against ground_truth.json, aggregated through the channel taxonomy.
The fitted model is saved to output/model.binpb for response_curves.py and
optimize_budget.py, so they don't have to refit.

This is an example, not a test: it is not run in CI.

Usage (from the repo root, after `dbt build` in integration_tests/):
    python examples/meridian/run_meridian.py            # quick smoke run, a few minutes on CPU
    python examples/meridian/run_meridian.py --full     # full sampling (use a GPU)
"""
import argparse
import sys
import time
from pathlib import Path

import duckdb
import pandas as pd

from common import (DEFAULT_OUT, DEFAULT_TRUTH, MODEL_FILE, REPO_ROOT, add_coverage, load_truth, print_table,
                    summarize_draws)

DEFAULT_DB = REPO_ROOT / "integration_tests" / "mmm_ready_dev.duckdb"
DEFAULT_TAXONOMY = REPO_ROOT / "seeds" / "channel_taxonomy.csv"

CONTROLS = ["avg_price", "promo_days", "holiday_days"]
# Sampling presets: (n_chains, n_adapt, n_burnin, n_keep, n_prior)
PRESETS = {
    "quick": (2, 200, 200, 200, 200),
    "full": (4, 500, 500, 1000, 500),
}


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=Path, default=DEFAULT_DB, help="DuckDB file with mmm_input_weekly")
    p.add_argument("--table", default="mmm_input_weekly", help="table to read (default: mmm_input_weekly)")
    p.add_argument("--ground-truth", type=Path, default=DEFAULT_TRUTH)
    p.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="directory for the saved model and CSVs")
    p.add_argument("--full", action="store_true", help="use the full sampling preset instead of the quick one")
    p.add_argument("--chains", type=int, help="override number of MCMC chains")
    p.add_argument("--n-adapt", type=int, help="override adaptation steps per chain")
    p.add_argument("--n-burnin", type=int, help="override burn-in steps per chain")
    p.add_argument("--n-keep", type=int, help="override kept draws per chain")
    p.add_argument("--n-prior", type=int, help="override number of prior draws")
    p.add_argument("--knots", type=int, default=39,
                   help="number of time knots for the baseline (default 39, ~1 per 4 weeks). Must be below the "
                        "number of weeks: holiday_days is the same in every geo and is unidentifiable with "
                        "one knot per week, Meridian's geo default")
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args()


def load_weekly(db_path: Path, table: str) -> pd.DataFrame:
    if not db_path.exists():
        sys.exit(f"{db_path} not found. Run `dbt seed && dbt build` in integration_tests/ first.")
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        tables = {r[0] for r in con.sql("select table_name from information_schema.tables").fetchall()}
        if table not in tables:
            sys.exit(f"Table {table} not found in {db_path}. Run `dbt build` in integration_tests/ first.")
        df = con.sql(f"select * from {table} order by geo, week_start").df()
    finally:
        con.close()
    return df


def media_channels(df: pd.DataFrame) -> list[str]:
    """mmm_channel names that have both spend_* and impressions_* columns (spend_unmapped excluded)."""
    channels = [c.removeprefix("spend_") for c in df.columns if c.startswith("spend_") and c != "spend_unmapped"]
    missing = [ch for ch in channels if f"impressions_{ch}" not in df.columns]
    if missing:
        sys.exit(f"Missing impressions_* columns for: {missing}")
    return channels


def build_input_data(df: pd.DataFrame, channels: list[str]):
    from meridian.data import load

    df = df.copy()
    df["week_start"] = pd.to_datetime(df["week_start"]).dt.strftime("%Y-%m-%d")
    # The synthetic panel has no population; a constant keeps geos on their own revenue scale.
    df["population"] = 1.0

    coord_to_columns = load.CoordToColumns(
        time="week_start",
        geo="geo",
        kpi="revenue",
        population="population",
        controls=CONTROLS,
        media=[f"impressions_{ch}" for ch in channels],
        media_spend=[f"spend_{ch}" for ch in channels],
    )
    loader = load.DataFrameDataLoader(
        df=df,
        coord_to_columns=coord_to_columns,
        kpi_type="revenue",
        media_to_channel={f"impressions_{ch}": ch for ch in channels},
        media_spend_to_channel={f"spend_{ch}": ch for ch in channels},
    )
    return loader.load()


def true_roi_by_channel(truth_path: Path, taxonomy_path: Path) -> pd.DataFrame:
    """Aggregate ground_truth.json (keyed by platform/campaign_type) to mmm_channel."""
    truth = load_truth(truth_path)["channels"]
    taxonomy = pd.read_csv(taxonomy_path)
    mapping = {f"{r.platform}/{r.campaign_type}": r.mmm_channel for r in taxonomy.itertuples()}

    rows = [
        {"mmm_channel": mapping.get(key, "unmapped"), "spend": t["spend"], "contribution": t["contribution"]}
        for key, t in truth.items()
    ]
    agg = pd.DataFrame(rows).groupby("mmm_channel", as_index=False)[["spend", "contribution"]].sum()
    agg["true_roi"] = agg["contribution"] / agg["spend"]
    return agg


def main():
    args = parse_args()
    n_chains, n_adapt, n_burnin, n_keep, n_prior = PRESETS["full" if args.full else "quick"]
    n_chains = args.chains or n_chains
    n_adapt = args.n_adapt or n_adapt
    n_burnin = args.n_burnin or n_burnin
    n_keep = args.n_keep or n_keep
    n_prior = args.n_prior or n_prior

    df = load_weekly(args.db, args.table)
    channels = media_channels(df)
    print(f"Loaded {len(df)} rows: {df['geo'].nunique()} geos, {df['week_start'].nunique()} weeks "
          f"({df['week_start'].min():%Y-%m-%d} .. {df['week_start'].max():%Y-%m-%d})")
    print(f"Media channels: {', '.join(channels)}")
    unmapped_share = df["spend_unmapped"].sum() / df[[f"spend_{c}" for c in channels] + ["spend_unmapped"]].sum().sum()
    print(f"Excluded spend_unmapped: {unmapped_share:.1%} of total spend")

    from meridian.analysis import analyzer
    from meridian.model import model, spec

    input_data = build_input_data(df, channels)
    mmm = model.Meridian(input_data=input_data, model_spec=spec.ModelSpec(knots=args.knots))

    print(f"Knots: {args.knots}")
    print(f"Sampling: prior={n_prior}, chains={n_chains}, adapt={n_adapt}, burnin={n_burnin}, keep={n_keep}")
    t0 = time.time()
    mmm.sample_prior(n_prior, seed=args.seed)
    mmm.sample_posterior(n_chains=n_chains, n_adapt=n_adapt, n_burnin=n_burnin, n_keep=n_keep, seed=args.seed)
    print(f"Sampling done in {time.time() - t0:.0f}s")

    from meridian.schema.serde import meridian_serde

    args.out.mkdir(parents=True, exist_ok=True)
    meridian_serde.save_meridian(mmm, str(args.out / MODEL_FILE))
    print(f"Saved fitted model to {args.out / MODEL_FILE}")

    an = analyzer.Analyzer(model_context=mmm.model_context, inference_data=mmm.inference_data)
    est = summarize_draws(an.roi(), input_data.media_channel.values, "roi")  # unmapped is not in the model

    truth = true_roi_by_channel(args.ground_truth, args.taxonomy)
    cmp = truth[["mmm_channel", "true_roi"]].merge(est, on="mmm_channel", how="outer")
    cmp = add_coverage(cmp, "true_roi", "roi").sort_values("mmm_channel").reset_index(drop=True)

    rhat = an.rhat_summary()
    max_rhat = float(rhat["max_r_hat"].max())

    print_table("ROI per mmm_channel (estimated vs ground truth):", cmp)
    print(f"\nMax R-hat across parameters: {max_rhat:.2f} (quick runs rarely converge; use --full for real numbers)")

    out_file = args.out / "roi_comparison.csv"
    cmp.to_csv(out_file, index=False)
    print(f"Written {out_file}")


if __name__ == "__main__":
    main()
