"""Tests for the best-time analyzer (v0.6.0)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from linkedin_mcp.best_time import BestTimeAnalyzer
from linkedin_mcp.db import DB


FIXED_NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "x.db")


def _insert(db: DB, when: datetime, status: str = "success", action: str = "post") -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO audit_log (action, target, status, dry_run, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (action, "self", status, 0, "{}", when.isoformat(timespec="seconds")),
        )


def test_empty_data_returns_safe_defaults(db: DB) -> None:
    a = BestTimeAnalyzer(db=db)
    r = a.recommend()
    assert r["data_points"] == 0
    assert "safe defaults" in r["note"].lower() or "no data" in r["note"].lower()
    assert len(r["best_slots"]) == 5
    # All hours present 0-23
    assert all(h in r["by_hour"] for h in range(24))
    # All 7 days present
    for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
        assert d in r["by_day_of_week"]


def test_single_post_appears_in_recommendation(db: DB) -> None:
    when = FIXED_NOW - timedelta(days=1)
    _insert(db, when)
    a = BestTimeAnalyzer(db=db)
    r = a.recommend(days=7)
    assert r["data_points"] == 1
    assert r["by_hour"][when.hour] == 1
    assert r["best_slots"][0]["post_count"] == 1
    assert r["best_slots"][0]["hour"] == when.hour


def test_skips_failed_posts(db: DB) -> None:
    when = FIXED_NOW - timedelta(days=1)
    _insert(db, when, status="failed")
    a = BestTimeAnalyzer(db=db)
    r = a.recommend()
    assert r["data_points"] == 0


def test_skips_old_data(db: DB) -> None:
    old = FIXED_NOW - timedelta(days=200)
    _insert(db, old)
    a = BestTimeAnalyzer(db=db)
    r = a.recommend(days=90)
    assert r["data_points"] == 0


def test_top_n_limits_recommendations(db: DB) -> None:
    # 3 different hours
    base = FIXED_NOW - timedelta(days=2)
    for h in (9, 10, 11):
        for _ in range(2 if h == 9 else 1):
            _insert(db, base.replace(hour=h))
    a = BestTimeAnalyzer(db=db)
    r = a.recommend(days=7, top_n=2)
    assert len(r["best_slots"]) == 2
    assert r["best_slots"][0]["post_count"] == 2  # hour 9 has 2 posts


def test_day_of_week_distribution(db: DB) -> None:
    # Mon = weekday 0
    base = FIXED_NOW - timedelta(days=10)  # 10 days ago
    _insert(db, base)
    a = BestTimeAnalyzer(db=db)
    r = a.recommend(days=14)
    assert r["data_points"] == 1
    # All days sum to data_points
    assert sum(r["by_day_of_week"].values()) == 1


def test_zero_fills_hours_and_days(db: DB) -> None:
    when = FIXED_NOW - timedelta(days=1)
    _insert(db, when)
    a = BestTimeAnalyzer(db=db)
    r = a.recommend(days=7)
    # Even though only 1 post, all 24 hours should be keys
    assert len(r["by_hour"]) == 24
    assert len(r["by_day_of_week"]) == 7