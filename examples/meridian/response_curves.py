"""
Compare Meridian's response curves, saturation, adstock and marginal ROI with the truth.

Loads the model saved by run_meridian.py (no refit) and the true curves that
scripts/generate_synthetic_data.py writes to ground_truth.json (`response_curves`).
Writes plots to output/plots/ and the numbers to output/*.csv.

All comparisons are by mmm_channel and on spend multipliers (1.0 = historical spend), so
the slightly different windows (the truth covers every generated day, Meridian only the
weeks kept in mmm_input_weekly) don't shift the curves sideways.

Usage (from the repo root):
    python examples/meridian/response_curves.py
"""
import argparse
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from common import DEFAULT_OUT, DEFAULT_TRUTH, add_coverage, load_model, load_truth, print_table, summarize_draws  # noqa: E402

# Reference palette (validated default, categorical slots 1-2) and chart ink.
MERIDIAN = "#2a78d6"
TRUTH = "#eb6834"
INK = "#0b0b0b"
MUTED = "#898781"
GRID = "#e1e0d9"
SURFACE = "#fcfcfb"

CURVE_POINTS = [0.5, 0.75, 1.0, 1.25, 1.5]
# Meridian evaluates one counterfactual per multiplier: a 0.1 grid plus the requested points.
MERIDIAN_GRID = sorted({round(0.1 * i, 2) for i in range(21)} | set(CURVE_POINTS))


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ground-truth", type=Path, default=DEFAULT_TRUTH)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="directory with model.binpb; plots go to <out>/plots")
    return p.parse_args()


def style_axes(ax, title):
    ax.set_facecolor(SURFACE)
    ax.set_title(title, color=INK, fontsize=10, loc="left")
    ax.grid(True, color=GRID, linewidth=0.6)
    ax.tick_params(colors=MUTED, labelsize=8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)


def small_multiples(channels, title):
    ncols = 3
    nrows = math.ceil(len(channels) / ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(4.2 * ncols, 3.2 * nrows), squeeze=False)
    fig.patch.set_facecolor(SURFACE)
    fig.suptitle(title, color=INK, fontsize=12, x=0.01, ha="left")
    for ax in axes.flat[len(channels):]:
        ax.set_visible(False)
    return fig, list(axes.flat[:len(channels)])


def finish(fig, path: Path, handles):
    fig.legend(handles=handles, loc="upper right", frameon=False, fontsize=9, ncols=len(handles))
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"Written {path}")


def legend_handles(meridian_label="Meridian (mean, 90% CI)"):
    return [
        plt.Line2D([], [], color=MERIDIAN, linewidth=2, label=meridian_label),
        plt.Line2D([], [], color=TRUTH, linewidth=2, linestyle="--", label="Truth"),
    ]


def response_curve_table(an, truth_curves, channels) -> pd.DataFrame:
    rc = an.response_curves(spend_multipliers=MERIDIAN_GRID)
    inc = rc["incremental_outcome"]
    rows = []
    for ch in channels:
        tc = truth_curves[ch]["curve"]
        true_inc = np.interp(MERIDIAN_GRID, tc["multiplier"], tc["incremental"])
        for i, m in enumerate(MERIDIAN_GRID):
            sel = inc.sel(channel=ch, spend_multiplier=m)
            rows.append({
                "mmm_channel": ch,
                "multiplier": m,
                "meridian_spend": float(rc["spend"].sel(channel=ch, spend_multiplier=m)),
                "true_incremental": float(true_inc[i]),
                "meridian_mean": float(sel.sel(metric="mean")),
                "meridian_p05": float(sel.sel(metric="ci_lo")),
                "meridian_p95": float(sel.sel(metric="ci_hi")),
            })
    return pd.DataFrame(rows)


def plot_response_curves(df, channels, path, normalize=False):
    title = ("Saturation: incremental revenue relative to the 100% level" if normalize
             else "Response curves: incremental revenue vs spend multiplier")
    fig, axes = small_multiples(channels, title)
    for ax, ch in zip(axes, channels):
        d = df[df["mmm_channel"] == ch]
        cols = ["true_incremental", "meridian_mean", "meridian_p05", "meridian_p95"]
        if normalize:
            at_one = d[d["multiplier"] == 1.0].iloc[0]
            # each curve divided by its own value at 100%: compares shape, not level
            vals = {"true_incremental": d["true_incremental"] / at_one["true_incremental"]}
            for c in cols[1:]:
                vals[c] = d[c] / at_one["meridian_mean"]
        else:
            vals = {c: d[c] / 1e6 for c in cols}
        ax.fill_between(d["multiplier"], vals["meridian_p05"], vals["meridian_p95"], color=MERIDIAN, alpha=0.18,
                        linewidth=0)
        ax.plot(d["multiplier"], vals["meridian_mean"], color=MERIDIAN, linewidth=2)
        ax.plot(d["multiplier"], vals["true_incremental"], color=TRUTH, linewidth=2, linestyle="--")
        ax.axvline(1.0, color=MUTED, linewidth=0.8, linestyle=":")
        style_axes(ax, ch)
        ax.set_xlabel("spend multiplier (1 = historical)", color=MUTED, fontsize=8)
        ax.set_ylabel("relative to 100%" if normalize else "incremental revenue (M)", color=MUTED, fontsize=8)
    finish(fig, path, legend_handles())


def plot_adstock(an, truth_curves, channels, path) -> pd.DataFrame:
    ad = an.adstock_decay()
    ad = ad[(ad["distribution"] == "posterior") & ad["is_int_time_unit"]]
    rows = []
    fig, axes = small_multiples(channels, "Adstock: weight by weekly lag (lag 0 = 1)")
    for ax, ch in zip(axes, channels):
        d = ad[ad["channel"] == ch].sort_values("time_units")
        w = np.array(truth_curves[ch]["adstock_weekly_weights"])
        w = w / w[0]  # Meridian reports alpha**lag, so put the truth on the same scale
        lags = d["time_units"].to_numpy()
        true_w = np.interp(lags, np.arange(len(w)), w)
        ax.fill_between(lags, d["ci_lo"], d["ci_hi"], color=MERIDIAN, alpha=0.18, linewidth=0)
        ax.plot(lags, d["mean"], color=MERIDIAN, linewidth=2, marker="o", markersize=4)
        ax.plot(lags, true_w, color=TRUTH, linewidth=2, linestyle="--", marker="s", markersize=4)
        style_axes(ax, ch)
        ax.set_xlabel("lag (weeks)", color=MUTED, fontsize=8)
        ax.set_ylim(-0.05, 1.05)
        rows += [{"mmm_channel": ch, "lag_weeks": int(lag), "true_weight": float(t), "meridian_mean": float(m),
                  "meridian_p05": float(lo), "meridian_p95": float(hi)}
                 for lag, t, m, lo, hi in zip(lags, true_w, d["mean"], d["ci_lo"], d["ci_hi"])]
    finish(fig, path, legend_handles())
    return pd.DataFrame(rows)


def plot_hill(an, channels, path):
    """Meridian's fitted Hill curves on its own media axis, with the historical media histogram."""
    hc = an.hill_curves()
    fig, axes = small_multiples(channels, "Meridian Hill curves (media units per geo-week; bars = historical weeks)")
    for ax, ch in zip(axes, channels):
        d = hc[(hc["channel"] == ch) & (hc["distribution"] == "posterior")].sort_values("media_units")
        hist = hc[(hc["channel"] == ch) & hc["scaled_count_histogram"].notna()].drop_duplicates(
            "start_interval_histogram")
        ax.bar(hist["start_interval_histogram"], hist["scaled_count_histogram"],
               width=hist["end_interval_histogram"] - hist["start_interval_histogram"], align="edge",
               color=MUTED, alpha=0.25, edgecolor=SURFACE, linewidth=1)
        ax.fill_between(d["media_units"], d["ci_lo"], d["ci_hi"], color=MERIDIAN, alpha=0.18, linewidth=0)
        ax.plot(d["media_units"], d["mean"], color=MERIDIAN, linewidth=2)
        style_axes(ax, ch)
        ax.set_xlabel("impressions per geo-week", color=MUTED, fontsize=8)
        ax.set_ylim(0, 1.02)
    finish(fig, path, [plt.Line2D([], [], color=MERIDIAN, linewidth=2, label="Meridian (mean, 90% CI)")])


def plot_mroi(df, path):
    fig, ax = plt.subplots(figsize=(7, 0.5 * len(df) + 1.6))
    fig.patch.set_facecolor(SURFACE)
    y = np.arange(len(df))
    ax.errorbar(df["mroi_mean"], y, xerr=[df["mroi_mean"] - df["mroi_p05"], df["mroi_p95"] - df["mroi_mean"]],
                fmt="o", color=MERIDIAN, markersize=8, linewidth=2, capsize=0)
    ax.scatter(df["true_mroi"], y, marker="D", s=64, color=TRUTH, edgecolor=SURFACE, linewidth=2, zorder=3)
    ax.axvline(1.0, color=MUTED, linewidth=0.8, linestyle=":")
    ax.set_yticks(y, df["mmm_channel"])
    ax.invert_yaxis()
    style_axes(ax, "Marginal ROI at historical spend (+1%)")
    ax.set_xlabel("incremental revenue per extra unit of spend (1 = break-even)", color=MUTED, fontsize=8)
    handles = [plt.Line2D([], [], color=MERIDIAN, marker="o", linewidth=2, label="Meridian (mean, 90% CI)"),
               plt.Line2D([], [], color=TRUTH, marker="D", linestyle="none", label="Truth")]
    fig.legend(handles=handles, frameon=False, fontsize=9, loc="upper right", ncols=2)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"Written {path}")


def main():
    args = parse_args()
    truth = load_truth(args.ground_truth)["response_curves"]
    mmm, an = load_model(args.out)
    channels = list(mmm.input_data.media_channel.values)
    plots = args.out / "plots"
    plots.mkdir(parents=True, exist_ok=True)

    # 1. Response curves and saturation shape
    rc = response_curve_table(an, truth, channels)
    rc.to_csv(args.out / "response_curves_comparison.csv", index=False)
    plot_response_curves(rc, channels, plots / "response_curves.png")
    plot_response_curves(rc, channels, plots / "saturation_normalized.png", normalize=True)
    points = rc[rc["multiplier"].isin(CURVE_POINTS)].copy()
    points["pct"] = (points["multiplier"] * 100).round().astype(int).astype(str) + "%"
    wide = points.pivot(index="mmm_channel", columns="pct", values=["true_incremental", "meridian_mean"]) / 1e6
    wide = wide.reindex(columns=[f"{int(m * 100)}%" for m in CURVE_POINTS], level=1)
    wide.columns = [f"{'true' if a == 'true_incremental' else 'mer'}_{b}" for a, b in wide.columns]
    print_table("Incremental revenue (M) at % of historical spend, truth vs Meridian mean:", wide.reset_index())

    # 2. Marginal ROI
    mroi = summarize_draws(an.marginal_roi(incremental_increase=0.01), channels, "mroi")
    mroi.insert(1, "true_mroi", [truth[ch]["mroi"] for ch in channels])
    mroi = add_coverage(mroi, "true_mroi", "mroi")
    mroi.to_csv(args.out / "mroi_comparison.csv", index=False)
    print_table("Marginal ROI at historical spend (+1%):", mroi)
    plot_mroi(mroi, plots / "mroi.png")

    # 3. Adstock and Hill
    adstock = plot_adstock(an, truth, channels, plots / "adstock.png")
    adstock.to_csv(args.out / "adstock_comparison.csv", index=False)
    plot_hill(an, channels, plots / "hill_curves.png")


if __name__ == "__main__":
    main()
