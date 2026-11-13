"""Read-only access to mmm_readiness_report, with business-language explanations.

Pure functions (no MCP dependency) so they can be tested without starting the server.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import duckdb

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "integration_tests" / "mmm_ready_dev.duckdb"
DB_PATH_ENV = "MMM_READY_DUCKDB_PATH"
SCHEMA_ENV = "MMM_READY_SCHEMA"
TABLE = "mmm_readiness_report"

FIX_DATA = "fix the data"
REVIEW = "review"
EXPERIMENT = "validate with an experiment"

BLOCKING_FLAGS = ("flag_unmapped_spend", "flag_short_history", "flag_kpi_gaps")
REVIEW_FLAGS = ("flag_platform_gaps", "flag_sparse", "flag_low_variance", "flag_spike", "flag_collinear")


class ReadinessError(RuntimeError):
    """Raised with a message meant to be shown to the user as-is."""


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.1%}"


def _num(x: float | None, fmt: str = "{:.2f}") -> str:
    return "n/a" if x is None else fmt.format(x)


def _explain_unmapped(r: dict) -> tuple[str, str]:
    return (
        f"{_pct(r['spend_share'])} of total spend comes from platform/campaign type combinations that "
        "are not in the channel taxonomy, so the model cannot attribute it to any channel and its "
        "effect leaks into the baseline or other channels.",
        "Add the missing (platform, campaign_type) rows to seeds/channel_taxonomy.csv and rebuild.",
    )


def _explain_short_history(r: dict) -> tuple[str, str]:
    return (
        f"Only {r['expected_weeks']} weeks of history are available. That is too little to separate "
        "marketing effects from seasonality and trend, so results would be unreliable.",
        "Load a longer history (ideally two years or more) before modeling.",
    )


def _explain_kpi_gaps(r: dict) -> tuple[str, str]:
    missing = (r["expected_weeks"] or 0) - (r["kpi_weeks"] or 0)
    return (
        f"The KPI (revenue/conversions) is missing for {missing} of {r['expected_weeks']} weeks. "
        "The model has nothing to explain in those weeks, which biases every channel.",
        "Backfill the missing outcome data in the source before modeling.",
    )


def _explain_platform_gaps(r: dict) -> tuple[str, str]:
    return (
        f"Spend for {r['platforms_with_gaps'] or 'some platforms'} disappears for {r['gap_weeks']} week(s) "
        "inside the period. It may be a real pause, or the data extraction may have failed for those weeks.",
        "Confirm with the channel owner whether the campaigns were actually off; if not, re-extract the data.",
    )


def _explain_sparse(r: dict) -> tuple[str, str]:
    return (
        f"The channel has no spend in {_pct(r['zero_week_share'])} of the weeks. With so few active weeks "
        "the model has little evidence to estimate its effect.",
        "Check whether it is a genuinely occasional channel; consider merging it with a related channel.",
    )


def _explain_low_variance(r: dict) -> tuple[str, str]:
    return (
        f"Weekly spend barely changes (coefficient of variation {_num(r['cv_weekly_spend'])}). "
        "If investment is always about the same, the data cannot show what happens when it goes up or down.",
        "Run a lift or geo experiment (vary spend on purpose) and use it as a prior or calibration.",
    )


def _explain_spike(r: dict) -> tuple[str, str]:
    return (
        f"One week has {_num(r['spike_ratio'], '{:.1f}')}x the average weekly spend. That is either a real "
        "burst or a data error (wrong currency, duplicated rows, an extra zero), and it can dominate the estimate.",
        "Check that week against the platform's billing; correct it if it is an error.",
    )


def _explain_collinear(r: dict) -> tuple[str, str]:
    return (
        f"Spend moves almost in lockstep with another channel (correlation {_num(r['max_abs_corr_with_other'])}). "
        "The model cannot tell which of the two drives the results and may split the credit arbitrarily.",
        "Run an experiment that moves one channel independently, or model the two together.",
    )


# flag column -> (short name, severity, action category, explainer)
FLAGS: dict[str, tuple[str, str, str, Any]] = {
    "flag_unmapped_spend": ("unmapped_spend", "blocking", FIX_DATA, _explain_unmapped),
    "flag_short_history": ("short_history", "blocking", FIX_DATA, _explain_short_history),
    "flag_kpi_gaps": ("kpi_gaps", "blocking", FIX_DATA, _explain_kpi_gaps),
    "flag_platform_gaps": ("platform_gaps", "review", REVIEW, _explain_platform_gaps),
    "flag_sparse": ("sparse", "review", REVIEW, _explain_sparse),
    "flag_low_variance": ("low_variance", "review", EXPERIMENT, _explain_low_variance),
    "flag_spike": ("spike", "review", REVIEW, _explain_spike),
    "flag_collinear": ("collinear", "review", EXPERIMENT, _explain_collinear),
}


def resolve_db_path(db_path: str | os.PathLike | None = None) -> Path:
    return Path(db_path or os.environ.get(DB_PATH_ENV) or DEFAULT_DB_PATH).expanduser()


def load_report(db_path: str | os.PathLike | None = None, schema: str | None = None) -> list[dict]:
    """Return every row of mmm_readiness_report as a dict, opening DuckDB read-only."""
    path = resolve_db_path(db_path)
    schema = schema or os.environ.get(SCHEMA_ENV) or "main"
    if not path.exists():
        raise ReadinessError(
            f"DuckDB file not found at {path}. Run `dbt build` in integration_tests/ first, "
            f"or set {DB_PATH_ENV} to the right path."
        )
    try:
        con = duckdb.connect(str(path), read_only=True)
    except duckdb.IOException as e:
        raise ReadinessError(
            f"Could not open {path} read-only ({e}). If `dbt build` is running, wait for it to finish."
        ) from e
    try:
        exists = con.execute(
            "select count(*) from information_schema.tables where table_schema = ? and table_name = ?",
            [schema, TABLE],
        ).fetchone()[0]
        if not exists:
            raise ReadinessError(
                f"Table {schema}.{TABLE} not found in {path}. Run `dbt build` in integration_tests/ first."
            )
        cur = con.execute(f'select * from "{schema}"."{TABLE}" order by mmm_channel')
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        con.close()


def _active_flags(row: dict, flags: tuple[str, ...] | None = None) -> list[str]:
    return [FLAGS[f][0] for f in (flags or tuple(FLAGS)) if row.get(f)]


def list_channels(db_path=None) -> list[dict]:
    return [
        {
            "channel": r["mmm_channel"],
            "status": r["readiness_status"],
            "total_spend": round(float(r["total_spend"] or 0), 2),
            "spend_share": round(float(r["spend_share"] or 0), 4),
        }
        for r in load_report(db_path)
    ]


def readiness_summary(db_path=None) -> dict:
    rows = load_report(db_path)
    counts = {"ready": 0, "review": 0, "blocked": 0}
    for r in rows:
        counts[r["readiness_status"]] = counts.get(r["readiness_status"], 0) + 1
    blocked = [
        {"channel": r["mmm_channel"], "blocking_issues": _active_flags(r, BLOCKING_FLAGS)}
        for r in rows
        if r["readiness_status"] == "blocked"
    ]
    review = [
        {"channel": r["mmm_channel"], "review_issues": _active_flags(r, REVIEW_FLAGS)}
        for r in rows
        if r["readiness_status"] == "review"
    ]
    return {
        "total_channels": len(rows),
        "counts_by_status": counts,
        "ready_for_modeling": not blocked,
        "blocked": blocked,
        "needs_review": review,
    }


def channel_readiness(channel: str, db_path=None) -> dict:
    rows = load_report(db_path)
    key = channel.strip().lower()
    row = next((r for r in rows if r["mmm_channel"] == key), None)
    if row is None:
        valid = ", ".join(r["mmm_channel"] for r in rows)
        raise ReadinessError(f"Unknown channel '{channel}'. Valid channels: {valid}.")
    issues = []
    for col, (name, severity, action, explain) in FLAGS.items():
        if row.get(col):
            explanation, next_step = explain(row)
            issues.append(
                {
                    "issue": name,
                    "severity": severity,
                    "explanation": explanation,
                    "recommended_action": action,
                    "next_step": next_step,
                }
            )
    return {
        "channel": row["mmm_channel"],
        "status": row["readiness_status"],
        "spend_share": round(float(row["spend_share"] or 0), 4),
        "issues": issues,
    }
