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
        "complete_weeks": 104,
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
        "min_history_weeks": 104,
        **{c: 0 for c in FLAG_COLS},
        "readiness_status": status,
        "kpi_source": "raw",
        "warn_kpi_from_analytics": 0,
    }
    base.update(flags)
    return base


def _write_db(path, rows):
    con = duckdb.connect(str(path))
    cols = list(rows[0])
    con.execute(
        "create table mmm_readiness_report as select * from (values "
        + ", ".join("(" + ", ".join("?" for _ in cols) + ")" for _ in rows)
        + ") t(" + ", ".join(cols) + ")",
        [v for r in rows for v in r.values()],
    )
    con.close()


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
    _write_db(path, rows)
    con = duckdb.connect(str(path))
    con.execute(
        "create table mmm_channel_correlations as select * from (values "
        "('search_brand', 'tv', 0.97, 1), ('social_prospecting', 'tv', 0.50, 0)"
        ") t(channel_a, channel_b, spend_correlation, is_collinear)"
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


def test_collinear_names_the_partner_channel(db):
    r = readiness.channel_readiness("tv", db)
    assert r["collinear_with"] == [{"channel": "search_brand", "correlation": 0.97}]
    (issue,) = r["issues"]
    assert "search_brand (correlation 0.97)" in issue["explanation"]
    assert "tv and search_brand" in issue["next_step"]
    # Pairs below the collinearity threshold are not listed.
    assert "social_prospecting" not in issue["explanation"]


def test_collinear_falls_back_without_correlations_table(db):
    con = duckdb.connect(str(db))
    con.execute("drop table mmm_channel_correlations")
    con.close()
    r = readiness.channel_readiness("tv", db)
    assert r["collinear_with"] == []
    assert "another channel (correlation 0.97)" in r["issues"][0]["explanation"]


def test_ready_channel_has_no_issues(db):
    assert readiness.channel_readiness("search_brand", db)["issues"] == []


def test_no_warnings_with_raw_kpi(db):
    assert readiness.readiness_summary(db)["warnings"] == []
    assert readiness.channel_readiness("tv", db)["warnings"] == []


@pytest.mark.parametrize("source,label", [("ga4", "GA4 (web analytics)"), ("firebase", "Firebase (app analytics)")])
def test_analytics_kpi_warning_is_informational(tmp_path, source, label):
    flags = {"kpi_source": source, "warn_kpi_from_analytics": 1}
    rows = [
        _row("search_brand", "ready", 1000.0, 0.9, **flags),
        _row("unmapped", "blocked", 100.0, 0.1, flag_unmapped_spend=1, **flags),
    ]
    path = tmp_path / "analytics.duckdb"
    _write_db(path, rows)

    s = readiness.readiness_summary(path)
    (w,) = s["warnings"]
    assert w["warning"] == "kpi_from_analytics" and w["severity"] == "info"
    assert label in w["explanation"] and "ERP" in w["next_step"]
    # The warning changes no status, count or issue list.
    assert s["counts_by_status"] == {"ready": 1, "review": 0, "blocked": 1}
    assert s["needs_review"] == []
    assert s["blocked"] == [{"channel": "unmapped", "blocking_issues": ["unmapped_spend"]}]

    r = readiness.channel_readiness("search_brand", path)
    assert r["status"] == "ready" and r["issues"] == []
    assert [x["warning"] for x in r["warnings"]] == ["kpi_from_analytics"]


def test_report_without_warning_columns_has_no_warnings(tmp_path):
    """Reports built before kpi_source existed still work."""
    row = _row("search_brand", "ready", 1.0, 1.0)
    del row["kpi_source"], row["warn_kpi_from_analytics"]
    path = tmp_path / "old.duckdb"
    _write_db(path, [row])
    assert readiness.readiness_summary(path)["warnings"] == []
    assert readiness.channel_readiness("search_brand", path)["warnings"] == []


def test_short_history_quotes_the_report_threshold(tmp_path):
    """The text follows mmm_ready_min_history_weeks as stored in the report, not a fixed number."""
    rows = [_row("tv", "blocked", 1.0, 1.0, expected_weeks=80, flag_short_history=1, min_history_weeks=52)]
    path = tmp_path / "history.duckdb"
    _write_db(path, rows)
    (issue,) = readiness.channel_readiness("tv", path)["issues"]
    assert issue["issue"] == "short_history"
    assert "Only 80 weeks" in issue["explanation"] and "minimum of 52" in issue["explanation"]
    assert issue["next_step"] == "Load at least 52 weeks of history before modeling."
    assert "two years" not in issue["explanation"] + issue["next_step"]

    # Reports built before min_history_weeks existed: no number is quoted.
    row = dict(rows[0])
    del row["min_history_weeks"]
    old = tmp_path / "history_old.duckdb"
    _write_db(old, [row])
    (issue,) = readiness.channel_readiness("tv", old)["issues"]
    assert "mmm_ready_min_history_weeks" in issue["explanation"]
    assert issue["next_step"] == "Load a longer history before modeling."


def test_every_warning_has_an_explanation():
    row = _row("x", "ready", 1.0, 1.0, kpi_source="ga4", warn_kpi_from_analytics=1)
    for _, (_, explain) in readiness.WARNINGS.items():
        explanation, next_step = explain(row)
        assert explanation and next_step


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
    assert [p["channel"] for p in readiness.channel_readiness("tv")["collinear_with"]] == ["video_online"]
    assert readiness.readiness_summary()["ready_for_modeling"] is False
    assert readiness.readiness_summary()["warnings"] == []  # synthetic KPI = raw backend data


@pytest.mark.skipif(not readiness.DEFAULT_DB_PATH.exists(), reason="run dbt build in integration_tests/ first")
def test_partial_edge_weeks_excluded_from_week_stats():
    """Synthetic data runs Sunday 2023-01-01 .. Wednesday 2025-12-31, so whatever mmm_ready_week_start
    the build used, at least one edge week is partial and must not count in per-week statistics."""
    con = duckdb.connect(str(readiness.DEFAULT_DB_PATH), read_only=True)
    (n_partial,) = con.execute(
        "select sum(is_partial_edge_week) from int_mmm__spend_week_calendar"
    ).fetchone()
    rows = con.execute(
        "select mmm_channel, expected_weeks, complete_weeks, weeks_with_spend "
        "from mmm_readiness_report"
    ).fetchall()
    con.close()
    assert n_partial >= 1
    assert rows
    for channel, expected, complete, with_spend in rows:
        assert complete == expected - n_partial, channel
        assert with_spend <= complete, channel
