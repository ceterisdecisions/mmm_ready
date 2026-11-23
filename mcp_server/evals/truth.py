"""Expected answers for the MCP evals, derived from the synthetic data's known truth.

Reads integration_tests/ground_truth.json (written by scripts/generate_synthetic_data.py) and
seeds/channel_taxonomy.csv. The only thing read from the built DuckDB is the week start the build
used, because the number of gap weeks depends on it.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
GROUND_TRUTH_PATH = REPO_ROOT / "integration_tests" / "ground_truth.json"
TAXONOMY_PATH = REPO_ROOT / "seeds" / "channel_taxonomy.csv"
CATALOG_PATH = Path(__file__).resolve().parent / "catalog.json"


@dataclass(frozen=True)
class Truth:
    # mmm_channel -> total spend in ground truth (platform/campaign_type aggregated through the taxonomy)
    spend_by_channel: dict[str, float]
    # (platform, campaign_type) -> mmm_channel, 'unmapped' if absent from the taxonomy
    channel_of: dict[tuple[str, str], str]
    gap_platform: str
    gap_start: date
    gap_end: date
    spike_platform: str
    spike_campaign_type: str
    spike_week: date
    collinear_platforms: tuple[str, str]
    unmapped_platform: str
    roi: dict[str, float]  # ground-truth key -> roi
    contribution: dict[str, float]  # ground-truth key -> contribution

    def channels_of_platform(self, platform: str) -> set[str]:
        return {ch for (p, _), ch in self.channel_of.items() if p == platform}


def load_catalog() -> list[dict]:
    return json.loads(CATALOG_PATH.read_text())["questions"]


def _parse_issue(issues: list[str], pattern: str) -> re.Match:
    for issue in issues:
        m = re.fullmatch(pattern, issue)
        if m:
            return m
    raise ValueError(f"No seeded issue matches {pattern!r} in {issues}: update mcp_server/evals/truth.py")


def load_truth(ground_truth_path: Path = GROUND_TRUTH_PATH, taxonomy_path: Path = TAXONOMY_PATH) -> Truth:
    gt = json.loads(Path(ground_truth_path).read_text())
    with open(taxonomy_path, newline="") as f:
        taxonomy = {(r["platform"], r["campaign_type"]): r["mmm_channel"] for r in csv.DictReader(f)}

    channel_of: dict[tuple[str, str], str] = {}
    spend: dict[str, float] = {}
    for key, ch in gt["channels"].items():
        platform, ctype = key.split("/")
        mmm = taxonomy.get((platform, ctype), "unmapped")
        channel_of[(platform, ctype)] = mmm
        spend[mmm] = spend.get(mmm, 0.0) + ch["spend"]

    issues = gt["seeded_issues"]
    gap = _parse_issue(issues, r"(\w+) gap (\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})")
    spike = _parse_issue(issues, r"(\w+) (\w+) x10 spike week of (\d{4}-\d{2}-\d{2})")
    coll = _parse_issue(issues, r"(\w+) collinear with (\w+)")
    unm = _parse_issue(issues, r"(\w+) unmapped in taxonomy")

    return Truth(
        spend_by_channel=spend,
        channel_of=channel_of,
        gap_platform=gap[1],
        gap_start=date.fromisoformat(gap[2]),
        gap_end=date.fromisoformat(gap[3]),
        spike_platform=spike[1],
        spike_campaign_type=spike[2],
        spike_week=date.fromisoformat(spike[3]),
        collinear_platforms=(coll[1], coll[2]),
        unmapped_platform=unm[1],
        roi={k: v["roi"] for k, v in gt["channels"].items()},
        contribution={k: v["contribution"] for k, v in gt["channels"].items()},
    )


def build_week_start_weekday(db_path: Path) -> int:
    """Weekday (Monday=0) the build's weeks start on, read from int_mmm__spend_week_calendar."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        weeks = [r[0] for r in con.execute("select week_start from int_mmm__spend_week_calendar").fetchall()]
    finally:
        con.close()
    weekdays = {w.weekday() for w in weeks}
    if len(weekdays) != 1:
        raise ValueError(f"Weeks start on several weekdays: {sorted(weekdays)}")
    return weekdays.pop()


def complete_weeks_inside(start: date, end: date, week_start_weekday: int) -> int:
    """Weeks that lie entirely inside [start, end]: those have no spend at all for the platform."""
    first = start + timedelta(days=(week_start_weekday - start.weekday()) % 7)
    n = 0
    while first + timedelta(days=6) <= end:
        n += 1
        first += timedelta(days=7)
    return n
