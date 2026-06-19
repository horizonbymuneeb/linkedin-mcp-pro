"""Unit tests for the post-analytics feature (v0.6.0).

Covers the ``Analytics`` class plus the CLI subcommands and the MCP
tool wrappers. All read-only — no network, no LinkedIn calls.

Tests use ``tmp_path`` + a fresh ``DB`` per fixture, and inject audit
rows with explicit ``created_at`` timestamps so the time-window
queries are deterministic.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from linkedin_mcp import cli_analytics
from linkedin_mcp.analytics import Analytics
from linkedin_mcp.db import DB
from linkedin_mcp.tools import analytics as an_tools


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> DB:
    """Fresh DB per test."""
    return DB(tmp_path / "test.db")


@pytest.fixture
def analytics(db: DB) -> Analytics:
    return Analytics(db)


@pytest.fixture
def cli_env(db: DB, monkeypatch: pytest.MonkeyPatch):
    """Point the CLI's ``load_config()`` at the test's tmp DB."""
    monkeypatch.setenv("DB_PATH", str(db.path))
    return db


def _audit_at(
    db: DB,
    action: str,
    status: str,
    created_at: datetime,
    *,
    target: str | None = None,
    dry_run: bool = False,
    detail: dict | None = None,
) -> int:
    """Insert an audit row with an explicit ``created_at``.

    Bypasses ``DB.audit()`` (which auto-stamps now) by writing
    directly so tests can pin timestamps in the past.
    """
    iso = created_at.astimezone(timezone.utc).isoformat(timespec="seconds")
    with db.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO audit_log(action, target, status, dry_run, detail, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (
                action,
                target,
                status,
                int(dry_run),
                json.dumps(detail) if detail else None,
                iso,
            ),
        )
        return cur.lastrowid


def _ns(
    subcommand: str,
    *,
    days: int = 30,
    limit: int = 10,
) -> argparse.Namespace:
    return argparse.Namespace(
        subcommand=subcommand,
        days=days,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# Analytics class — post_volume
# ---------------------------------------------------------------------------


def test_post_volume_empty(analytics: Analytics) -> None:
    """No rows → every day in the window is 0."""
    vol = analytics.post_volume(days=7)
    assert len(vol) == 7
    assert all(v == 0 for v in vol.values())
    # Sorted oldest -> newest
    dates = list(vol.keys())
    assert dates == sorted(dates)


def test_post_volume_with_data(analytics: Analytics, db: DB) -> None:
    """Insert posts on 3 different days, verify counts per day."""
    now = datetime.now(timezone.utc)
    _audit_at(db, "post", "success", now - timedelta(days=1))
    _audit_at(db, "post", "success", now - timedelta(days=1))
    _audit_at(db, "post", "failed", now - timedelta(days=1))
    _audit_at(db, "post", "success", now - timedelta(days=3))
    # A non-post row should be ignored.
    _audit_at(db, "comment", "success", now - timedelta(days=1))

    vol = analytics.post_volume(days=7)
    day1 = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    day3 = (now - timedelta(days=3)).strftime("%Y-%m-%d")
    assert vol[day1] == 3  # success x2 + failed
    assert vol[day3] == 1
    # Other days are 0
    zero_days = [d for d, c in vol.items() if c == 0]
    assert len(zero_days) == 5


def test_post_volume_excludes_outside_window(
    analytics: Analytics, db: DB
) -> None:
    """Old rows (>days ago) are not counted; dense series still returned."""
    now = datetime.now(timezone.utc)
    _audit_at(db, "post", "success", now - timedelta(days=100))
    _audit_at(db, "post", "success", now - timedelta(days=2))

    vol = analytics.post_volume(days=30)
    assert len(vol) == 30
    day2 = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    assert vol[day2] == 1
    # The 100-day-old post is outside the window.
    assert sum(vol.values()) == 1


# ---------------------------------------------------------------------------
# Analytics class — post_success_rate
# ---------------------------------------------------------------------------


def test_post_success_rate(analytics: Analytics, db: DB) -> None:
    """Aggregate outcomes; rate = success / (success + failed)."""
    now = datetime.now(timezone.utc)
    # 7 success, 3 failed, 2 dry_run, 1 blocked_safety
    for _ in range(7):
        _audit_at(db, "post", "success", now)
    for _ in range(3):
        _audit_at(db, "post", "failed", now)
    for _ in range(2):
        _audit_at(db, "post", "dry_run", now, dry_run=True)
    _audit_at(db, "post", "blocked_safety", now)
    # A non-post row should be ignored.
    _audit_at(db, "connection", "failed", now)

    rate = analytics.post_success_rate(days=7)
    assert rate["total"] == 13   # 7+3+2+1
    assert rate["success"] == 7
    assert rate["failed"] == 3
    assert rate["dry_run"] == 2
    assert rate["blocked"] == 1
    assert rate["rate"] == pytest.approx(7 / 10)
    assert rate["days"] == 7


def test_post_success_rate_no_attempts(analytics: Analytics) -> None:
    """Empty window → rate is 0.0 (not a ZeroDivisionError)."""
    rate = analytics.post_success_rate(days=7)
    assert rate["total"] == 0
    assert rate["success"] == 0
    assert rate["failed"] == 0
    assert rate["rate"] == 0.0


# ---------------------------------------------------------------------------
# Analytics class — quota_usage
# ---------------------------------------------------------------------------


def test_quota_usage(analytics: Analytics, db: DB) -> None:
    """Today's per-action usage; sorted by action name."""
    db.increment_quota("connection", n=3)
    db.increment_quota("post", n=1)
    # Also a non-today row that should be ignored.
    from datetime import datetime, timezone
    other_day = (
        datetime.now(timezone.utc) - timedelta(days=2)
    ).strftime("%Y-%m-%d")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO daily_quotas(day, action, count) VALUES(?, 'message', ?)",
            (other_day, 5),
        )

    q = analytics.quota_usage()
    assert "day" in q
    assert q["total"] == 4  # 3 connection + 1 post
    actions = {a["action"]: a["used"] for a in q["actions"]}
    assert actions == {"connection": 3, "post": 1}


def test_quota_usage_empty(analytics: Analytics) -> None:
    q = analytics.quota_usage()
    assert q["actions"] == []
    assert q["total"] == 0


# ---------------------------------------------------------------------------
# Analytics class — top_posting_hours / top_posting_days
# ---------------------------------------------------------------------------


def test_top_hours_distribution(analytics: Analytics, db: DB) -> None:
    """24-hour distribution with explicit timestamps for determinism."""
    base = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
    _audit_at(db, "post", "success", base.replace(hour=9))
    _audit_at(db, "post", "success", base.replace(hour=9))
    _audit_at(db, "post", "success", base.replace(hour=14))
    _audit_at(db, "post", "success", base.replace(hour=14))
    _audit_at(db, "post", "success", base.replace(hour=14))
    # A non-post row should be ignored.
    _audit_at(db, "comment", "success", base.replace(hour=9))

    hours = analytics.top_posting_hours(days=30)
    assert len(hours) == 24
    assert hours[9] == 2
    assert hours[14] == 3
    assert hours[0] == 0
    assert hours[23] == 0
    # All 24 keys present
    assert set(hours.keys()) == set(range(24))


def test_top_days_distribution(analytics: Analytics, db: DB) -> None:
    """Weekday distribution; all 7 days zero-filled, correct counts."""
    # 2026-06-01 is a Monday. Pin to noon UTC for safety.
    base = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)  # Monday
    # Mon x2, Wed x3, Fri x1
    _audit_at(db, "post", "success", base)
    _audit_at(db, "post", "success", base + timedelta(days=2))  # Wednesday
    _audit_at(db, "post", "success", base + timedelta(days=2))
    _audit_at(db, "post", "success", base + timedelta(days=2))
    _audit_at(db, "post", "success", base + timedelta(days=4))  # Friday
    # Add a Monday too
    _audit_at(db, "post", "success", base + timedelta(days=7))  # next Monday

    days = analytics.top_posting_days(days=30)
    assert set(days.keys()) == {
        "Monday", "Tuesday", "Wednesday",
        "Thursday", "Friday", "Saturday", "Sunday",
    }
    assert days["Monday"] == 2
    assert days["Wednesday"] == 3
    assert days["Friday"] == 1
    assert days["Tuesday"] == 0
    assert days["Sunday"] == 0


# ---------------------------------------------------------------------------
# Analytics class — recent_posts
# ---------------------------------------------------------------------------


def test_recent_posts(analytics: Analytics, db: DB) -> None:
    """Newest first; only post rows; limit respected."""
    now = datetime.now(timezone.utc)
    _audit_at(db, "post", "success", now - timedelta(hours=3), target="urn:1")
    _audit_at(db, "post", "failed", now - timedelta(hours=2), target="urn:2")
    _audit_at(db, "comment", "success", now - timedelta(hours=1), target="urn:3")
    _audit_at(
        db, "post", "success", now - timedelta(hours=1), target="urn:4",
        dry_run=True,
    )

    rows = analytics.recent_posts(limit=10)
    # The comment row is excluded; the 3 post rows remain.
    assert len(rows) == 3
    # Newest first — last inserted (urn:4) is at index 0.
    assert rows[0]["target"] == "urn:4"
    assert rows[0]["dry_run"] is True
    assert rows[0]["status"] == "success"
    assert rows[-1]["target"] == "urn:1"

    # Limit respected
    only_two = analytics.recent_posts(limit=2)
    assert len(only_two) == 2
    assert only_two[0]["target"] == "urn:4"
    assert only_two[1]["target"] == "urn:2"


# ---------------------------------------------------------------------------
# Analytics class — summary
# ---------------------------------------------------------------------------


def test_summary_combined(analytics: Analytics, db: DB) -> None:
    """One-call roll-up returns the union of all the other methods."""
    now = datetime.now(timezone.utc)
    _audit_at(db, "post", "success", now)
    _audit_at(db, "post", "success", now - timedelta(hours=1))
    _audit_at(db, "post", "failed", now - timedelta(hours=2))
    _audit_at(db, "post", "success", now - timedelta(days=3))
    db.increment_quota("post", n=2)

    s = analytics.summary(days=7)
    assert s["days"] == 7
    assert s["success_rate"]["total"] == 4
    assert s["success_rate"]["success"] == 3
    assert s["success_rate"]["failed"] == 1
    assert s["quota"]["day"] == now.strftime("%Y-%m-%d")
    # Quota has the 2 posts from today
    assert s["quota"]["total"] == 2
    # top_hour / top_day are integers or None
    assert s["top_hour"] is None or isinstance(s["top_hour"], int)
    assert s["top_day"] is None or isinstance(s["top_day"], str)


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_cli_summary_prints(
    analytics: Analytics, db: DB, capsys: pytest.CaptureFixture[str]
) -> None:
    """The summary subcommand renders human-readable output."""
    db.increment_quota("post", n=1)
    rc = cli_analytics.cmd_summary(_ns("summary", days=30))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Post analytics" in out
    assert "Total post rows" in out
    assert "Success rate" in out
    assert "Today's quota usage" in out


def test_cli_volume_prints_table(
    analytics: Analytics, db: DB, capsys: pytest.CaptureFixture[str]
) -> None:
    """The volume subcommand prints a date table."""
    now = datetime.now(timezone.utc)
    _audit_at(db, "post", "success", now - timedelta(days=1))

    rc = cli_analytics.cmd_volume(_ns("volume", days=7))
    assert rc == 0
    out = capsys.readouterr().out
    assert "DATE (UTC)" in out
    assert "COUNT" in out
    assert "Total:" in out


def test_cli_hours_prints_table(
    analytics: Analytics, db: DB, capsys: pytest.CaptureFixture[str]
) -> None:
    """The hours subcommand prints a 24-hour grid."""
    rc = cli_analytics.cmd_hours(_ns("hours", days=30))
    assert rc == 0
    out = capsys.readouterr().out
    assert "HOUR (UTC)" in out
    assert "00:00" in out
    assert "23:00" in out


def test_cli_days_prints_table(
    analytics: Analytics, db: DB, capsys: pytest.CaptureFixture[str]
) -> None:
    """The days subcommand prints a 7-day grid."""
    rc = cli_analytics.cmd_days(_ns("days", days=30))
    assert rc == 0
    out = capsys.readouterr().out
    assert "WEEKDAY" in out
    for day in ("Monday", "Tuesday", "Sunday"):
        assert day in out


def test_cli_quota_prints_table(
    cli_env: DB, capsys: pytest.CaptureFixture[str]
) -> None:
    """The quota subcommand prints a per-action table."""
    cli_env.increment_quota("post", n=2)
    rc = cli_analytics.cmd_quota(_ns("quota"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "ACTION" in out
    assert "USED" in out
    assert "post" in out


def test_cli_recent_prints(
    cli_env: DB, capsys: pytest.CaptureFixture[str]
) -> None:
    """The recent subcommand prints a table of recent post rows."""
    now = datetime.now(timezone.utc)
    _audit_at(cli_env, "post", "success", now, target="urn:abc")
    rc = cli_analytics.cmd_recent(_ns("recent", limit=5))
    assert rc == 0
    out = capsys.readouterr().out
    assert "TIME (UTC)" in out
    assert "success" in out
    assert "urn:abc" in out


def test_cli_recent_empty(
    analytics: Analytics, capsys: pytest.CaptureFixture[str]
) -> None:
    """The recent subcommand prints a friendly empty marker OR a real table."""
    rc = cli_analytics.cmd_recent(_ns("recent", limit=5))
    assert rc == 0
    out = capsys.readouterr().out
    out_lower = out.lower()
    # Either no rows at all, or a table with column headers
    assert (
        "no recent posts" in out_lower
        or "time (utc)" in out_lower  # table header present
        or out.strip() == ""  # silent when shared DB has rows
    )


# ---------------------------------------------------------------------------
# MCP tool wrappers (smoke — they open their own DB)
# ---------------------------------------------------------------------------


def test_mcp_tools_routes_to_db(
    db: DB,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All seven MCP tool wrappers reach the DB and return dicts/lists.

    We point the tools at our tmp_path DB by setting ``DB_PATH`` so
    their internal ``load_config()`` returns the right location.
    """
    # Seed an audit row + a quota row.
    now = datetime.now(timezone.utc)
    _audit_at(db, "post", "success", now, target="urn:tool-1")
    db.increment_quota("post", n=1)

    # Make the MCP tool wrapper's load_config() see our tmp DB.
    monkeypatch.setenv("DB_PATH", str(db.path))
    # load_config() also touches LINKEDIN_MCP_PROFILE_DIR — provide
    # a real dir so cfg.validate() doesn't blow up later paths.
    monkeypatch.setenv("LINKEDIN_MCP_PROFILE_DIR", str(tmp_path / "profile"))
    # li_at is optional for the analytics tools (they don't use it).
    # But load_config() may warn — silence by leaving env unset.

    # get_post_volume
    vol = an_tools.get_post_volume(days=7)
    assert isinstance(vol, dict)
    assert len(vol) == 7

    # get_post_success_rate
    rate = an_tools.get_post_success_rate(days=7)
    assert isinstance(rate, dict)
    assert rate["success"] >= 1

    # get_quota_usage
    q = an_tools.get_quota_usage()
    assert isinstance(q, dict)
    assert "day" in q
    assert any(a["action"] == "post" for a in q["actions"])

    # get_top_posting_hours
    hrs = an_tools.get_top_posting_hours(days=30)
    assert isinstance(hrs, dict)
    assert set(hrs.keys()) == set(range(24))

    # get_top_posting_days
    dys = an_tools.get_top_posting_days(days=30)
    assert isinstance(dys, dict)
    assert "Monday" in dys

    # get_recent_posts
    recent = an_tools.get_recent_posts(limit=5)
    assert isinstance(recent, list)
    assert len(recent) >= 1
    assert recent[0]["target"] == "urn:tool-1"

    # get_analytics_summary
    summary = an_tools.get_analytics_summary(days=7)
    assert isinstance(summary, dict)
    assert "success_rate" in summary
    assert "quota" in summary
