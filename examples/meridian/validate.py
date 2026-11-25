"""
Validate a Meridian run against the synthetic ground truth and write one report.

Reads what the other scripts left in output/ (no model loading, no refit):
    run_config.json, convergence.csv   <- run_meridian.py
    roi_comparison.csv                 <- run_meridian.py
    mroi_comparison.csv                <- response_curves.py
    budget_allocation_comparison.csv,
    budget_summary.csv                 <- optimize_budget.py

and writes output/validation_report.md (human-readable) and output/validation_report.csv
(one row per check). The first line of the report is the convergence verdict. Unless the run
converged by the strict criterion, the report says the ROI numbers are not reliable estimates.

Pass/fail rules are heuristics, not a statistical test: the truth inside the 90% credible
interval (ROI, mROI), and a positive share of the possible improvement captured by Meridian's
budget allocation. Channels affected by the issues planted in the synthetic data are labeled,
but their checks count like any other.

Usage (from the repo root, after the three other scripts):
    python examples/meridian/validate.py
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from common import CONVERGENCE_FILE, DEFAULT_OUT, RUN_CONFIG_FILE, VERDICT_TEXT

# Channels touched by the issues planted by scripts/generate_synthetic_data.py (ground_truth.json -> seeded_issues).
SEEDED_NOTES = {
    "tv": "collinear with video_online (YouTube generated from TV)",
    "video_online": "collinear with tv (YouTube generated from TV)",
    "social_prospecting": "x10 Meta spike week of 2024-06-03 and TikTok gap 2024-09-02..2024-09-22",
    "unmapped": "Pinterest missing from the taxonomy; excluded from the model",
}


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="directory with the other scripts' outputs")
    return p.parse_args()


def read(out: Path, name: str) -> Path:
    path = out / name
    if not path.exists():
        sys.exit(f"{path} not found. Run run_meridian.py, response_curves.py and optimize_budget.py first.")
    return path


def fmt(x, spec=",.3f"):
    return "n/a" if pd.isna(x) else format(x, spec)


def md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in df.itertuples(index=False):
        lines.append("| " + " | ".join(fmt(v) if isinstance(v, float) else str(v) for v in row) + " |")
    return "\n".join(lines)


def main():
    args = parse_args()
    cfg = json.loads(read(args.out, RUN_CONFIG_FILE).read_text())
    conv = pd.read_csv(read(args.out, CONVERGENCE_FILE))
    roi = pd.read_csv(read(args.out, "roi_comparison.csv"))
    mroi = pd.read_csv(read(args.out, "mroi_comparison.csv"))
    alloc = pd.read_csv(read(args.out, "budget_allocation_comparison.csv"))
    summary = pd.read_csv(read(args.out, "budget_summary.csv")).set_index("metric")["value"]

    verdict = cfg["verdict"]
    checks = []  # (area, item, value, truth, passed, note)

    # ROI per channel
    roi["rel_error"] = (roi["roi_mean"] - roi["true_roi"]) / roi["true_roi"]
    est = roi.dropna(subset=["roi_mean"]).copy()
    est["true_rank"] = est["true_roi"].rank(ascending=False).astype(int)
    est["est_rank"] = est["roi_mean"].rank(ascending=False).astype(int)
    roi = roi.merge(est[["mmm_channel", "true_rank", "est_rank"]], on="mmm_channel", how="left")
    spearman = est["true_roi"].corr(est["roi_mean"], method="spearman")
    for r in roi.itertuples():
        passed = None if pd.isna(r.roi_mean) else bool(r.in_90ci == True)  # noqa: E712 (nullable boolean)
        checks.append(("roi_in_90ci", r.mmm_channel, r.roi_mean, r.true_roi, passed, SEEDED_NOTES.get(r.mmm_channel, "")))

    # mROI per channel
    for r in mroi.itertuples():
        checks.append(("mroi_in_90ci", r.mmm_channel, r.mroi_mean, r.true_mroi, bool(r.in_90ci == True),  # noqa: E712
                       SEEDED_NOTES.get(r.mmm_channel, "")))

    # Budget optimizer
    alloc["same_direction"] = ((alloc["meridian_mult"] - 1) * (alloc["true_opt_mult"] - 1)) > 0
    captured = float(summary["share of possible improvement captured"])
    checks.append(("optimizer_captured_gt_0", "all", captured, 1.0, bool(captured > 0), ""))

    report = pd.DataFrame(checks, columns=["check", "item", "estimate", "truth", "passed", "seeded_issue"])
    report.insert(0, "verdict", verdict)
    report.to_csv(args.out / "validation_report.csv", index=False)

    scored = report[report["passed"].notna()]
    n_fail = int((scored["passed"] == False).sum())  # noqa: E712
    w = cfg["worst"]
    lines = [f"**Convergence: {VERDICT_TEXT[verdict]}**", ""]
    if verdict != "converged":
        lines += ["> The model did not converge by the strict criterion, so the ROI, mROI and budget numbers "
                  "below are **not reliable estimates**. They are shown to document the run, not to be used.", ""]
    lines += [
        "# Meridian validation against the synthetic ground truth",
        "",
        f"Single run, seed {cfg['seed']}, synthetic data. Thresholds are heuristics. "
        f"{n_fail} of {len(scored)} scored checks fail.",
        "",
        "## Run",
        "",
        f"- Preset `{cfg['preset']}`: {cfg['n_chains']} chains, adapt {cfg['n_adapt']}, burn-in {cfg['n_burnin']}, "
        f"keep {cfg['n_keep']}, prior {cfg['n_prior']}; knots {cfg['knots']}",
        f"- Data: {cfg['n_geos']} geos, {cfg['n_weeks']} weeks, channels {', '.join(cfg['channels'])}",
        f"- Sampling time {cfg['sampling_seconds']:.0f}s on {', '.join(cfg['jax_devices'])}",
        f"- Versions: {', '.join(f'{k} {v}' for k, v in cfg['versions'].items())}",
        "",
        "## Convergence",
        "",
        f"| diagnostic | worst value | threshold |\n|---|---|---|\n"
        f"| R-hat, rank-normalized (arviz) | {w['max_rhat_rank']:.3f} | <= {cfg['thresholds']['rhat_rank_max']} |\n"
        f"| ESS bulk | {w['min_ess_bulk']:.0f} | >= {cfg['thresholds']['ess_min']} |\n"
        f"| ESS tail | {w['min_ess_tail']:.0f} | >= {cfg['thresholds']['ess_min']} |\n"
        f"| Divergences | {w['divergences']} | 0 |\n"
        f"| R-hat, Meridian (classic) | {w['max_rhat_meridian']:.3f} | < {cfg['thresholds']['rhat_meridian_max']} "
        f"(Meridian default, reported only) |",
        "",
    ]
    failing = conv[conv["fails_strict"]]
    if len(failing):
        lines += [f"Parameters failing the strict criterion ({len(failing)} of {len(conv)}):", "",
                  md_table(failing.drop(columns="fails_strict")), ""]

    roi_view = roi[["mmm_channel", "true_roi", "roi_mean", "roi_p05", "roi_p95", "rel_error", "in_90ci",
                    "true_rank", "est_rank"]].copy()
    roi_view[["true_rank", "est_rank"]] = roi_view[["true_rank", "est_rank"]].astype("Int64").astype(str)
    roi_view["in_90ci"] = roi_view["in_90ci"].astype(str)
    roi_view = roi_view.replace({"nan": "n/a", "<NA>": "n/a"})
    roi_view["seeded_issue"] = roi_view["mmm_channel"].map(SEEDED_NOTES).fillna("")
    mroi_view = mroi.copy()
    mroi_view["in_90ci"] = mroi_view["in_90ci"].astype(str)
    mroi_view["seeded_issue"] = mroi_view["mmm_channel"].map(SEEDED_NOTES).fillna("")
    alloc_view = alloc[["mmm_channel", "meridian_mult", "true_opt_mult", "same_direction"]].copy()
    alloc_view["same_direction"] = alloc_view["same_direction"].astype(str)

    lines += [
        "## ROI per channel",
        "",
        md_table(roi_view),
        "",
        f"Spearman rank correlation between true and estimated ROI: {fmt(spearman)}",
        "",
        "## Marginal ROI at historical spend (+1%)",
        "",
        md_table(mroi_view),
        "",
        "## Budget optimizer (fixed budget, scored on the true curves)",
        "",
        md_table(alloc_view),
        "",
        f"- Share of the possible improvement captured: {fmt(captured, '.1%')}",
        f"- True improvement of Meridian's allocation: {fmt(float(summary['true improvement of Meridian allocation']), ',.0f')}",
        f"- Improvement Meridian predicted for it: {fmt(float(summary['improvement Meridian predicted for its allocation']), ',.0f')}",
        f"- Possible improvement (true optimum - historical): {fmt(float(summary['possible improvement (true optimum - historical)']), ',.0f')}",
        "",
        "## Failed checks",
        "",
    ]
    fails = scored[scored["passed"] == False]  # noqa: E712
    lines += [md_table(fails.drop(columns=["verdict", "passed"])) if len(fails) else "None.", ""]

    text = "\n".join(lines)
    (args.out / "validation_report.md").write_text(text)
    print(text)
    print(f"Written {args.out / 'validation_report.md'} and {args.out / 'validation_report.csv'}")


if __name__ == "__main__":
    main()
