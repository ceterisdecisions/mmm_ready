"""Shared paths and helpers for the Meridian example scripts."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRUTH = REPO_ROOT / "integration_tests" / "ground_truth.json"
DEFAULT_OUT = Path(__file__).resolve().parent / "output"
MODEL_FILE = "model.binpb"
CONVERGENCE_FILE = "convergence.csv"
RUN_CONFIG_FILE = "run_config.json"

# Convergence criterion. Heuristics after Vehtari et al. (2021), not validated on this model.
RHAT_STRICT = 1.01      # rank-normalized split R-hat (arviz), every parameter
ESS_MIN = 400           # bulk and tail ESS over all chains, every parameter
MERIDIAN_RHAT = 1.2     # Meridian's own default (classic R-hat, ConvergenceConfig.convergence_threshold)

VERDICT_TEXT = {
    "converged": "CONVERGED (R-hat <= 1.01, ESS bulk/tail >= 400, no divergences)",
    "acceptable": "NOT CONVERGED by the strict criterion; only Meridian's default R-hat < 1.2 holds",
    "not_converged": "NOT CONVERGED (fails even Meridian's default R-hat < 1.2)",
}


def convergence_verdict(rhat_rank: float, ess_bulk: float, ess_tail: float, divergences: int,
                        meridian_rhat: float) -> str:
    """converged / acceptable / not_converged from the worst value of each diagnostic."""
    if rhat_rank <= RHAT_STRICT and ess_bulk >= ESS_MIN and ess_tail >= ESS_MIN and divergences == 0:
        return "converged"
    if meridian_rhat < MERIDIAN_RHAT:
        return "acceptable"
    return "not_converged"


def load_truth(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"{path} not found. Run scripts/generate_synthetic_data.py first.")
    truth = json.loads(path.read_text())
    if "response_curves" not in truth:
        sys.exit(f"{path} has no response_curves. Regenerate it with the current scripts/generate_synthetic_data.py.")
    return truth


def load_model(out_dir: Path):
    """Load the model fitted and saved by run_meridian.py, plus an Analyzer on it."""
    from meridian.analysis import analyzer
    from meridian.schema.serde import meridian_serde

    path = out_dir / MODEL_FILE
    if not path.exists():
        sys.exit(f"{path} not found. Run examples/meridian/run_meridian.py first.")
    mmm = meridian_serde.load_meridian(str(path))
    an = analyzer.Analyzer(model_context=mmm.model_context, inference_data=mmm.inference_data)
    return mmm, an


def summarize_draws(values, channels, prefix: str) -> pd.DataFrame:
    """Posterior mean and 90% interval per channel from a (chains, draws, channels) tensor."""
    arr = np.asarray(values)
    draws = arr.reshape(-1, arr.shape[-1])
    return pd.DataFrame({
        "mmm_channel": list(channels),
        f"{prefix}_mean": draws.mean(axis=0),
        f"{prefix}_p05": np.quantile(draws, 0.05, axis=0),
        f"{prefix}_p95": np.quantile(draws, 0.95, axis=0),
    })


def add_coverage(df: pd.DataFrame, truth_col: str, prefix: str) -> pd.DataFrame:
    """Add in_90ci: whether the truth falls inside the 90% interval (NA when not estimated)."""
    in_ci = (df[truth_col] >= df[f"{prefix}_p05"]) & (df[truth_col] <= df[f"{prefix}_p95"])
    df["in_90ci"] = in_ci.astype("boolean").mask(df[f"{prefix}_mean"].isna())
    return df


def print_table(title: str, df: pd.DataFrame):
    pd.set_option("display.width", 140)
    print(f"\n{title}")
    print(df.to_string(index=False, float_format=lambda x: f"{x:,.3f}"))
