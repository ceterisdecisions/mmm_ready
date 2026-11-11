"""
Run Meridian's budget optimizer and score its recommendation against the true optimum.

1. Meridian: BudgetOptimizer with a fixed budget equal to historical total spend, and each
   channel's spend kept within ±--spend-bounds (default 30%, Meridian's own default).
2. Truth: the same problem solved on the true response curves in ground_truth.json
   (PCHIP interpolation of the 0-2x grid, SLSQP with several starting points).
3. Both allocations, plus the historical one, are scored with the TRUE curves, and the
   script reports how much of the possible improvement Meridian's recommendation captures:

       captured = (R_meridian - R_historical) / (R_true_optimum - R_historical)

Allocations are compared as multipliers of each channel's historical spend, so the
slightly different windows (truth: every generated day; Meridian: weeks kept in
mmm_input_weekly) don't matter. Unmapped spend (Pinterest) is not part of the model and
stays fixed.

Usage (from the repo root, after run_meridian.py):
    python examples/meridian/optimize_budget.py [--spend-bounds 0.3]
"""
import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.interpolate import PchipInterpolator  # noqa: E402
from scipy.optimize import minimize  # noqa: E402

from common import DEFAULT_OUT, DEFAULT_TRUTH, load_model, load_truth, print_table  # noqa: E402

MERIDIAN = "#2a78d6"
TRUTH = "#eb6834"
INK = "#0b0b0b"
MUTED = "#898781"
GRID = "#e1e0d9"
SURFACE = "#fcfcfb"


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ground-truth", type=Path, default=DEFAULT_TRUTH)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="directory with model.binpb; results go here")
    p.add_argument("--spend-bounds", type=float, default=0.3,
                   help="max relative change per channel vs historical spend, both ways (default 0.3 = ±30%%)")
    p.add_argument("--starts", type=int, default=20, help="random starting points for the true optimum (default 20)")
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args()


def meridian_allocation(mmm, an, bounds: float) -> pd.DataFrame:
    from meridian.analysis import optimizer

    channels = list(mmm.input_data.media_channel.values)
    # Exact historical spend per channel over the modeled window (Meridian rounds its own).
    hist = mmm.input_data.media_spend.sum(dim=[d for d in mmm.input_data.media_spend.dims if d != "media_channel"])
    res = optimizer.BudgetOptimizer(analyzer=an).optimize(
        fixed_budget=True, spend_constraint_lower=bounds, spend_constraint_upper=bounds)
    opt, non = res.optimized_data, res.nonoptimized_data
    df = pd.DataFrame({
        "mmm_channel": channels,
        "meridian_hist_spend": hist.sel(media_channel=channels).values,
        "meridian_opt_spend": opt["spend"].sel(channel=channels).values,
        "meridian_pred_hist_inc": non["incremental_outcome"].sel(channel=channels, metric="mean").values,
        "meridian_pred_opt_inc": opt["incremental_outcome"].sel(channel=channels, metric="mean").values,
    })
    df["meridian_mult"] = df["meridian_opt_spend"] / df["meridian_hist_spend"]
    return df


def true_optimum(curves: dict, channels, spend, bounds: float, starts: int, seed: int) -> np.ndarray:
    """Maximize sum of true incremental revenue at fixed total spend, multipliers in [1-b, 1+b]."""
    f = [PchipInterpolator(curves[ch]["curve"]["multiplier"], curves[ch]["curve"]["incremental"]) for ch in channels]
    df = [fi.derivative() for fi in f]
    scale = sum(fi(1.0) for fi in f)  # keep the objective O(1) for SLSQP

    def neg_total(m):
        return -sum(fi(mi) for fi, mi in zip(f, m)) / scale

    def neg_grad(m):
        return -np.array([dfi(mi) for dfi, mi in zip(df, m)]) / scale

    budget = {"type": "eq", "fun": lambda m: (m @ spend - spend.sum()) / spend.sum(), "jac": lambda m: spend / spend.sum()}
    box = [(1 - bounds, 1 + bounds)] * len(channels)

    rng = np.random.default_rng(seed)
    inits = [np.ones(len(channels))] + [rng.uniform(1 - bounds, 1 + bounds, len(channels)) for _ in range(starts)]
    best = None
    for x0 in inits:
        r = minimize(neg_total, x0, jac=neg_grad, bounds=box, constraints=[budget], method="SLSQP",
                     options={"ftol": 1e-12, "maxiter": 500})
        feasible = abs(r.x @ spend - spend.sum()) / spend.sum() < 1e-6
        if r.success and feasible and (best is None or r.fun < best.fun):
            best = r
    if best is None:
        raise RuntimeError("SLSQP found no feasible optimum for the true curves")
    return best.x


def plot_allocation(df: pd.DataFrame, bounds: float, path: Path):
    fig, ax = plt.subplots(figsize=(8, 0.6 * len(df) + 1.6))
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    ax.set_axisbelow(True)
    y = np.arange(len(df))
    h = 0.36
    for off, col, color in [(-h / 2, "meridian_mult", MERIDIAN), (h / 2, "true_opt_mult", TRUTH)]:
        ax.barh(y + off, (df[col] - 1) * 100, height=h, color=color, edgecolor=SURFACE, linewidth=2)
    for b in (-bounds, bounds):
        ax.axvline(b * 100, color=MUTED, linewidth=0.8, linestyle=":")
    ax.axvline(0, color=MUTED, linewidth=1)
    ax.set_yticks(y, df["mmm_channel"])
    ax.invert_yaxis()
    ax.set_title(f"Budget reallocation vs historical spend (fixed budget, ±{bounds:.0%} per channel)",
                 color=INK, fontsize=10, loc="left")
    ax.set_xlabel("change in channel spend (%)", color=MUTED, fontsize=8)
    ax.grid(True, axis="x", color=GRID, linewidth=0.6)
    ax.tick_params(colors=MUTED, labelsize=8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    handles = [plt.Rectangle((0, 0), 1, 1, color=MERIDIAN, label="Meridian recommendation"),
               plt.Rectangle((0, 0), 1, 1, color=TRUTH, label="True optimum")]
    fig.legend(handles=handles, frameon=False, fontsize=9, loc="upper right", ncols=2)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"Written {path}")


def main():
    args = parse_args()
    curves = load_truth(args.ground_truth)["response_curves"]
    mmm, an = load_model(args.out)

    df = meridian_allocation(mmm, an, args.spend_bounds)
    channels = list(df["mmm_channel"])
    true_spend = np.array([curves[ch]["spend"] for ch in channels])
    df["true_hist_spend"] = true_spend
    df["true_opt_mult"] = true_optimum(curves, channels, true_spend, args.spend_bounds, args.starts, args.seed)

    true_f = {ch: PchipInterpolator(curves[ch]["curve"]["multiplier"], curves[ch]["curve"]["incremental"])
              for ch in channels}
    df["true_inc_hist"] = [float(true_f[ch](1.0)) for ch in channels]
    df["true_inc_meridian"] = [float(true_f[ch](m)) for ch, m in zip(channels, df["meridian_mult"])]
    df["true_inc_opt"] = [float(true_f[ch](m)) for ch, m in zip(channels, df["true_opt_mult"])]

    r_hist, r_mer, r_opt = df["true_inc_hist"].sum(), df["true_inc_meridian"].sum(), df["true_inc_opt"].sum()
    possible = r_opt - r_hist
    captured = (r_mer - r_hist) / possible if possible > 0 else float("nan")
    mer_pred_uplift = df["meridian_pred_opt_inc"].sum() - df["meridian_pred_hist_inc"].sum()
    # Meridian's multipliers applied to the truth's spend base: how far off the fixed budget that lands.
    budget_gap = (df["meridian_mult"] @ true_spend - true_spend.sum()) / true_spend.sum()

    table = df[["mmm_channel", "meridian_mult", "true_opt_mult", "true_inc_hist", "true_inc_meridian", "true_inc_opt"]]
    print_table("Allocation as multiplier of historical spend, scored with the TRUE curves (incremental revenue):",
                table)
    summary = pd.DataFrame([
        ("budget (historical total, modeled channels)", true_spend.sum()),
        ("true incremental revenue: historical allocation", r_hist),
        ("true incremental revenue: Meridian allocation", r_mer),
        ("true incremental revenue: true optimum", r_opt),
        ("possible improvement (true optimum - historical)", possible),
        ("true improvement of Meridian allocation", r_mer - r_hist),
        ("improvement Meridian predicted for its allocation", mer_pred_uplift),
        ("share of possible improvement captured", captured),
        ("budget gap of Meridian multipliers on truth spend", budget_gap),
    ], columns=["metric", "value"])
    print_table("Summary:", summary)

    df.to_csv(args.out / "budget_allocation_comparison.csv", index=False)
    summary.to_csv(args.out / "budget_summary.csv", index=False)
    print(f"Written {args.out / 'budget_allocation_comparison.csv'} and {args.out / 'budget_summary.csv'}")
    (args.out / "plots").mkdir(parents=True, exist_ok=True)
    plot_allocation(df, args.spend_bounds, args.out / "plots" / "budget_allocation.png")


if __name__ == "__main__":
    main()
