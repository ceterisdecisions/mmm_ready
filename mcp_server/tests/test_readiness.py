import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import readiness  # noqa: E402

FLAG_COLS = list(readiness.FLAGS)


def _row(channel, status, spend, share, **flags):
    base = {
        "mmm_channel": channel,
        "expected_weeks": 104,
        "kpi_weeks": 104,
        "weeks_with_spend": 104,
        "gap_weeks": 0,
        "platforms_with_gaps": None,
        "total_spend": spend,
        "spend_share": share,
        "zero_week_share": 0.0,
        "cv_weekly_spend": 0.4,
        "spike_ratio": 1.5,
        "max_abs_corr_with_other": 0.3,
        **{c: 0 for c in FLAG_COLS},
        "readiness_status": status,
    }
    base.update(flags)
    return base


@pytest.fixture
def db(tmp_path):
    rows = [
        _row("search_brand", "ready", 1000.0, 0.5),
        _row("tv", "review", 600.0, 0.3, flag_collinear=1, max_abs_corr_with_other=0.97),
        _row("social_prospecting", "review", 300.0, 0.15,
             flag_spike=1, spike_ratio=9.8, flag_platform_gaps=1, gap_weeks=3, platforms_with_gaps="tiktok"),
        _row("unmapped", "blocked", 100.0, 0.05, flag_unmapped_spend=1),
    ]
    path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(path))
    cols = list(rows[0])
    con.execute(
        "create table mmm_readiness_report as select * from (values "
        + ", ".join("(" + ", ".join("?" for _ in cols) + ")" for _ in rows)
        + ") t(" + ", ".join(cols) + ")",
        [v for r in rows for v in r.values()],
    )
    con.close()
    return path


def test_list_channels(db):
    channels = readiness.list_channels(db)
    assert [c["channel"] for c in channels] == ["search_brand", "social_prospecting", "tv", "unmapped"]
    assert {c["channel"]: c["status"] for c in channels}["unmapped"] == "blocked"


def test_summary_counts_and_blockers(db):
    s = readiness.readiness_summary(db)
    assert s["total_channels"] == 4
    assert s["counts_by_status"] == {"ready": 1, "review": 2, "blocked": 1}
    assert s["ready_for_modeling"] is False
    assert s["blocked"] == [{"channel": "unmapped", "blocking_issues": ["unmapped_spend"]}]
    review = {r["channel"]: r["review_issues"] for r in s["needs_review"]}
    assert review == {"social_prospecting": ["platform_gaps", "spike"], "tv": ["collinear"]}


def test_channel_readiness_explains_and_recommends(db):
    r = readiness.channel_readiness("Social_Prospecting ", db)
    assert r["status"] == "review"
    by_issue = {i["issue"]: i for i in r["issues"]}
    assert set(by_issue) == {"platform_gaps", "spike"}
    assert "tiktok" in by_issue["platform_gaps"]["explanation"]
    assert "9.8x" in by_issue["spike"]["explanation"]
    assert by_issue["spike"]["recommended_action"] == readiness.REVIEW


@pytest.mark.parametrize(
    "channel,issue,action",
    [("tv", "collinear", readiness.EXPERIMENT), ("unmapped", "unmapped_spend", readiness.FIX_DATA)],
)
def test_action_categories(db, channel, issue, action):
    issues = readiness.channel_readiness(channel, db)["issues"]
    assert [(i["issue"], i["recommended_action"]) for i in issues] == [(issue, action)]


def test_ready_channel_has_no_issues(db):
    assert readiness.channel_readiness("search_brand", db)["issues"] == []


def test_every_flag_has_an_explanation():
    row = _row("x", "review", 1.0, 1.0, **{c: 1 for c in FLAG_COLS})
    for _, (_, severity, action, explain) in readiness.FLAGS.items():
        explanation, next_step = explain(row)
        assert explanation and next_step
        assert severity in {"blocking", "review"}
        assert action in {readiness.FIX_DATA, readiness.REVIEW, readiness.EXPERIMENT}


def test_unknown_channel_lists_valid_ones(db):
    with pytest.raises(readiness.ReadinessError, match="Valid channels: search_brand"):
        readiness.channel_readiness("pinterest", db)


def test_missing_file(tmp_path):
    with pytest.raises(readiness.ReadinessError, match="Run `dbt build`"):
        readiness.list_channels(tmp_path / "nope.duckdb")


def test_missing_table(tmp_path):
    path = tmp_path / "empty.duckdb"
    duckdb.connect(str(path)).close()
    with pytest.raises(readiness.ReadinessError, match="not found.*Run `dbt build`"):
        readiness.readiness_summary(path)


def test_db_path_from_env(db, monkeypatch):
    monkeypatch.setenv(readiness.DB_PATH_ENV, str(db))
    assert len(readiness.list_channels()) == 4


def test_opens_read_only(db):
    readiness.list_channels(db)
    # A read-only connection must not have created a WAL or changed the file.
    assert not Path(str(db) + ".wal").exists()


@pytest.mark.skipif(not readiness.DEFAULT_DB_PATH.exists(), reason="run dbt build in integration_tests/ first")
def test_seeded_issues_on_synthetic_data():
    """The four issues planted by scripts/generate_synthetic_data.py must surface through the tools."""
    def issues(ch):
        return {i["issue"] for i in readiness.channel_readiness(ch)["issues"]}

    assert "unmapped_spend" in issues("unmapped")  # Pinterest is not in the taxonomy
    assert "spike" in issues("social_prospecting")  # x10 Meta prospecting week
    gaps = readiness.channel_readiness("social_prospecting")["issues"]
    assert any(i["issue"] == "platform_gaps" and "tiktok" in i["explanation"] for i in gaps)  # TikTok gap
    assert "collinear" in issues("video_online") and "collinear" in issues("tv")  # YouTube ~ TV
    assert readiness.readiness_summary()["ready_for_modeling"] is False
