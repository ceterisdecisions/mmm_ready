"""MCP evals: the questions in mcp_server/evals/catalog.json, asked through a real MCP client.

The server is started once over stdio (as Claude Code does) and every tool call the catalog needs is
made up front. The checks are deterministic: expected answers come from ground_truth.json and the
taxonomy (mcp_server/evals/truth.py) or from the baseline pinned in the catalog. No model is called.

Skipped unless the synthetic DuckDB and ground_truth.json exist (generate data + dbt build first).
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path

import anyio
import pytest

MCP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MCP_DIR))
sys.path.insert(0, str(MCP_DIR / "evals"))

import readiness  # noqa: E402
import truth as truth_mod  # noqa: E402

DB_PATH = readiness.resolve_db_path()
CATALOG = truth_mod.load_catalog()
QUESTIONS = {q["id"]: q for q in CATALOG}
SPEND_REL_TOL = 1e-6
# Known limitation (see catalog Q10): ground truth counts TikTok spend inside the seeded gap.
SPEND_MATCH_EXCLUDED = {"social_prospecting"}

needs_data = pytest.mark.skipif(
    not (DB_PATH.exists() and truth_mod.GROUND_TRUTH_PATH.exists()),
    reason="generate synthetic data and run dbt build in integration_tests/ first",
)


def _payload(result):
    """Tool result as Python data: dict tools return JSON text, list tools structured {'result': [...]}."""
    if result.is_error:
        return {"error": " ".join(c.text for c in result.content)}
    if result.structured_content is not None and set(result.structured_content) == {"result"}:
        return result.structured_content["result"]
    (content,) = result.content
    return json.loads(content.text)


async def _ask_server() -> dict:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(MCP_DIR / "server.py")],
        env={**os.environ, readiness.DB_PATH_ENV: str(DB_PATH)},
    )
    out: dict = {}
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            out["tools"] = (await session.list_tools()).tools
            out["summary"] = _payload(await session.call_tool("readiness_summary", {}))
            out["list"] = _payload(await session.call_tool("list_channels", {}))
            out["channel"] = {}
            for name in [c["channel"] for c in out["list"]] + ["pinterest"]:
                out["channel"][name] = _payload(await session.call_tool("channel_readiness", {"channel": name}))
    return out


@pytest.fixture(scope="module")
def mcp():
    return anyio.run(_ask_server)


@pytest.fixture(scope="module")
def truth():
    return truth_mod.load_truth()


def _issues(mcp, channel) -> dict[str, dict]:
    return {i["issue"]: i for i in mcp["channel"][channel]["issues"]}


def _only_channel(truth, platform) -> str:
    (channel,) = truth.channels_of_platform(platform)
    return channel


def _rel_close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=SPEND_REL_TOL)


# --- catalog ---------------------------------------------------------------------------------------

def test_catalog_ids_have_a_test():
    tests = {n.removeprefix("test_").split("_")[0].upper() for n in globals() if re.match(r"test_q\d+_", n)}
    assert tests == set(QUESTIONS)


def test_readme_lists_the_catalog():
    readme = (MCP_DIR / "README.md").read_text()
    section = readme.split("## Evals", 1)[1].split("\n## ", 1)[0]
    ids = re.findall(r"^\| (Q\d+) \|", section, flags=re.M)
    assert ids == [q["id"] for q in CATALOG]
    for q in CATALOG:
        row = next(line for line in section.splitlines() if line.startswith(f"| {q['id']} |"))
        # Rows of questions with a 'limited' note must say so ("Limited check" / "Known limitation").
        assert ("limit" in row.lower()) == ("limited" in q), q["id"]


# --- questions -------------------------------------------------------------------------------------

@needs_data
def test_q1_is_data_ready(mcp):
    base = QUESTIONS["Q1"]["baseline"]
    s = mcp["summary"]
    assert s["ready_for_modeling"] is False
    assert s["total_channels"] == base["total_channels"]
    assert s["counts_by_status"] == base["counts_by_status"]


@needs_data
def test_q2_what_blocks(mcp, truth):
    unmapped = _only_channel(truth, truth.unmapped_platform)
    assert unmapped == "unmapped"
    assert mcp["summary"]["blocked"] == [{"channel": unmapped, "blocking_issues": ["unmapped_spend"]}]
    issue = _issues(mcp, unmapped)["unmapped_spend"]
    assert issue["severity"] == "blocking" and issue["recommended_action"] == readiness.FIX_DATA
    assert "channel_taxonomy.csv" in issue["next_step"]
    total = sum(c["total_spend"] for c in mcp["list"])
    expected_share = truth.spend_by_channel[unmapped] / total
    assert issue["explanation"].startswith(f"{expected_share:.1%} of total spend")


@needs_data
def test_q3_what_needs_review(mcp, truth):
    review = {r["channel"]: r["review_issues"] for r in mcp["summary"]["needs_review"]}
    assert review == QUESTIONS["Q3"]["baseline"]["needs_review"]
    # The seeded review issues are part of it.
    assert "platform_gaps" in review[_only_channel(truth, truth.gap_platform)]
    assert "spike" in review[truth.channel_of[(truth.spike_platform, truth.spike_campaign_type)]]
    for platform in truth.collinear_platforms:
        assert "collinear" in review[_only_channel(truth, platform)]


@needs_data
def test_q4_tiktok_gap_and_spike(mcp, truth):
    channel = _only_channel(truth, truth.gap_platform)
    r = mcp["channel"][channel]
    assert r["status"] == "review"
    issues = _issues(mcp, channel)
    weeks = truth_mod.complete_weeks_inside(
        truth.gap_start, truth.gap_end, truth_mod.build_week_start_weekday(DB_PATH)
    )
    assert weeks >= 1
    gap = issues["platform_gaps"]
    assert gap["severity"] == "review" and gap["recommended_action"] == readiness.REVIEW
    assert f"Spend for {truth.gap_platform} disappears for {weeks} week(s)" in gap["explanation"]
    assert truth.channel_of[(truth.spike_platform, truth.spike_campaign_type)] == channel
    assert issues["spike"]["recommended_action"] == readiness.REVIEW


@needs_data
def test_q5_tv_youtube_collinear(mcp, truth):
    a, b = (_only_channel(truth, p) for p in truth.collinear_platforms)
    for channel, partner in [(a, b), (b, a)]:
        r = mcp["channel"][channel]
        assert r["status"] == "review"
        assert [p["channel"] for p in r["collinear_with"]] == [partner]
        issue = _issues(mcp, channel)["collinear"]
        assert issue["recommended_action"] == readiness.EXPERIMENT
        assert partner in issue["explanation"]


@needs_data
def test_q6_where_is_pinterest(mcp, truth):
    err = mcp["channel"][truth.unmapped_platform]["error"]
    assert f"Unknown channel '{truth.unmapped_platform}'" in err
    valid = err.split("Valid channels: ", 1)[1].rstrip(".").split(", ")
    assert valid == [c["channel"] for c in mcp["list"]]
    unmapped = next(c for c in mcp["list"] if c["channel"] == "unmapped")
    assert unmapped["status"] == "blocked"
    assert _rel_close(unmapped["total_spend"], truth.spend_by_channel["unmapped"])


@needs_data
def test_q7_history_long_enough(mcp):
    # Limited: the tools expose no history length unless short_history is flagged.
    assert all("short_history" not in b["blocking_issues"] for b in mcp["summary"]["blocked"])
    for channel, r in mcp["channel"].items():
        if "error" not in r:
            assert "short_history" not in _issues(mcp, channel), channel


@needs_data
def test_q8_kpi_source(mcp):
    # Limited: the tools expose kpi_source only through the kpi_from_analytics warning.
    assert mcp["summary"]["warnings"] == []
    for channel, r in mcp["channel"].items():
        if "error" not in r:
            assert r["warnings"] == [], channel


@needs_data
def test_q9_ready_channels(mcp):
    ready = [c["channel"] for c in mcp["list"] if c["status"] == "ready"]
    assert ready == QUESTIONS["Q9"]["baseline"]["ready"]


@needs_data
def test_q10_spend_per_channel(mcp, truth):
    listed = {c["channel"]: c for c in mcp["list"]}
    assert set(listed) == set(truth.spend_by_channel)
    for channel, expected in truth.spend_by_channel.items():
        if channel in SPEND_MATCH_EXCLUDED:
            continue
        assert _rel_close(listed[channel]["total_spend"], expected), channel
    # spend_share is rounded to 4 decimals per channel.
    assert abs(sum(c["spend_share"] for c in mcp["list"]) - 1) <= 5e-5 * len(listed)


@needs_data
def test_q11_roi_is_not_answered(mcp, truth):
    forbidden = re.compile(r"roi|contribution|incremental|return on", re.I)
    for tool in mcp["tools"]:
        text = " ".join([tool.name, tool.description or "", json.dumps(tool.input_schema)])
        assert not forbidden.search(text), tool.name

    outputs = [mcp["summary"], mcp["list"], *mcp["channel"].values()]

    def keys(x):
        if isinstance(x, dict):
            for k, v in x.items():
                yield k
                yield from keys(v)
        elif isinstance(x, list):
            for v in x:
                yield from keys(v)

    for out in outputs:
        assert not any(forbidden.search(k) for k in keys(out))
    blob = json.dumps(outputs)
    assert not re.search(r"\bROI\b|contribution|incremental", blob, re.I)
    # No ground-truth ROI or contribution figure appears anywhere in the answers.
    # ROIs at their stored 3 decimals (shorter forms would collide with unrelated small numbers);
    # contributions to the cent and to the unit.
    figures = [f"{v:.3f}" for v in truth.roi.values()]
    figures += [f for v in truth.contribution.values() for f in (f"{v:.2f}", f"{round(v):d}")]
    for text in figures:
        assert not re.search(rf"(?<![\d.]){re.escape(text)}(?![\d])", blob), text
