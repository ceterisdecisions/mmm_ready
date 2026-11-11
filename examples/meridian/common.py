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
