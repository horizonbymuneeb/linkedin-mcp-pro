"""Tests for multi-platform + AI engagement coach (v1.0.0)."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

from linkedin_mcp.coach import EngagementCoach
from linkedin_mcp.db import DB
from linkedin_mcp.multi_platform import (
    BaseAdapter,
    MultiPlatformError,
    Post,
    TwitterAdapter,
    cross_post,
    get_adapter,
    list_platforms,
)


# ---------------------------------------------------------------------------
# Multi-platform
# ---------------------------------------------------------------------------


def test_list_platforms() -> None:
    ps = list_platforms()
    names = [p["name"] for p in ps]
    assert "twitter" in names
    assert "threads" in names
    assert "bluesky" in names
    assert "mastodon" in names


def test_get_adapter_twitter() -> None:
    a = get_adapter("twitter")
    assert isinstance(a, TwitterAdapter)
    assert a.char_limit == 280


def test_get_adapter_unknown_raises() -> None:
    with pytest.raises(MultiPlatformError):
        get_adapter("myspace")


def test_twitter_strips_newlines() -> None:
    a = TwitterAdapter()
    out = a.format(Post(text="hello\nworld\nfoo"))
    assert "\n" not in out


def test_twitter_truncates_long() -> None:
    a = TwitterAdapter()
    long = "x" * 500
    out = a.format(Post(text=long))
    assert len(out) <= a.char_limit


def test_cross_post_returns_each_platform() -> None:
    p = Post(text="hello world")
    out = cross_post(p, ["twitter", "bluesky"])
    assert "twitter" in out
    assert "bluesky" in out
    assert out["twitter"]["stub"] is True


def test_cross_post_empty_platforms_raises() -> None:
    with pytest.raises(MultiPlatformError):
        cross_post(Post(text="x"), [])


def test_cross_post_attaches_link() -> None:
    p = Post(text="hi", link="https://example.com")
    out = cross_post(p, ["twitter"])
    assert "https://example.com" in out["twitter"]["would_post"]


# ---------------------------------------------------------------------------
# Engagement coach
# ---------------------------------------------------------------------------


FIXED_NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path: Path) -> DB:
    return DB(tmp_path / "x.db")


def _insert_post(db: DB, days_ago: float, status: str = "success") -> None:
    when = FIXED_NOW - timedelta(days=days_ago)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO audit_log (action, target, status, dry_run, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("post", "self", status, 0, "{}", when.isoformat(timespec="seconds")),
        )


def test_coach_no_data_high_priority(db: DB) -> None:
    c = EngagementCoach(db=db)
    report = c.report()
    assert report["tip_count"] >= 1
    assert any(t["priority"] == "high" for t in report["tips"])


def test_coach_low_volume_tip(db: DB) -> None:
    for _ in range(3):  # < 2/week
        _insert_post(db, 0)
    c = EngagementCoach(db=db)
    report = c.report()
    assert any("cadence" in t["title"].lower() for t in report["tips"])


def test_coach_good_volume_no_cadence_tip(db: DB) -> None:
    # 15 posts across the window = healthy cadence
    for i in range(15):
        _insert_post(db, i * 2)
    c = EngagementCoach(db=db)
    report = c.report()
    assert not any("cadence" in t["title"].lower() for t in report["tips"])


def test_coach_failure_rate_tip(db: DB) -> None:
    # Mostly failed posts
    for _ in range(4):
        _insert_post(db, 1, status="failed")
    _insert_post(db, 2, status="success")
    c = EngagementCoach(db=db)
    report = c.report()
    assert any("failure" in t["title"].lower() for t in report["tips"])


def test_coach_top_day_tip(db: DB) -> None:
    # Insert 5 posts on the same day as 'today' (FIXED_NOW - 1 day is
    # safely within the 90-day window regardless of when the test runs).
    for _ in range(5):
        _insert_post(db, 1)
    c = EngagementCoach(db=db)
    report = c.report()
    titles = " ".join(t["title"] for t in report["tips"]).lower()
    # Should mention a day of week (full or 3-letter abbrev)
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
            "mon", "tue", "tues", "wed", "weds", "thu", "thur", "thurs", "fri", "sat", "sun"]
    assert any(d in titles for d in days)


def test_coach_summary_field_present(db: DB) -> None:
    c = EngagementCoach(db=db)
    report = c.report()
    assert "summary" in report
    assert "generated_at" in report
    assert "window_days" in report