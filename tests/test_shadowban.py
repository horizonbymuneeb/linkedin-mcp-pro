"""Tests for shadow-ban detector (v0.7.0)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from linkedin_mcp.db import DB
from linkedin_mcp.shadowban import ShadowBanDetector


FIXED_NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "x.db")


@pytest.fixture
def det(db: DB) -> ShadowBanDetector:
    return ShadowBanDetector(db=db)


def _record(
    det: ShadowBanDetector, target: str, impressions: int, engagement: int, when: datetime = FIXED_NOW
) -> None:
    det.record_post_metrics(target, impressions, engagement, when=when)


def test_no_data_with_zero_posts(det: ShadowBanDetector) -> None:
    r = det.check()
    assert r["status"] == "no_data"
    assert r["data_points"] == 0


def test_no_data_with_few_posts(det: ShadowBanDetector) -> None:
    for i in range(3):
        _record(det, f"post{i}", 1000, 50, when=FIXED_NOW - timedelta(days=i))
    r = det.check(min_baseline_posts=5)
    assert r["status"] == "no_data"
    assert r["data_points"] == 3


def test_ok_when_engagement_stable(det: ShadowBanDetector) -> None:
    for i in range(8):
        _record(det, f"post{i}", 1000, 50, when=FIXED_NOW - timedelta(days=i))
    r = det.check()
    assert r["status"] == "ok"
    assert r["data_points"] == 8


def test_warning_on_mild_drop(det: ShadowBanDetector) -> None:
    # 7 normal posts, then 1 with 30% drop (above 25% but below 50%)
    for i in range(1, 8):
        _record(det, f"post{i}", 1000, 100, when=FIXED_NOW - timedelta(days=i))
    _record(det, "post0", 1000, 70, when=FIXED_NOW)  # 30% drop
    r = det.check()
    assert r["status"] in ("warning", "alert")
    assert r["drop_pct"] > 0.25


def test_alert_on_major_drop(det: ShadowBanDetector) -> None:
    # 7 normal posts then 1 with 90% drop
    for i in range(1, 8):
        _record(det, f"post{i}", 1000, 100, when=FIXED_NOW - timedelta(days=i))
    _record(det, "post0", 1000, 10, when=FIXED_NOW)
    r = det.check()
    assert r["status"] == "alert"
    assert any("shadow-ban" in a.lower() or "below baseline" in a.lower() for a in r["alerts"])


def test_alert_on_consecutive_low(det: ShadowBanDetector) -> None:
    # 7 normal posts then 3 in a row with <20% engagement
    for i in range(3, 10):
        _record(det, f"post{i}", 1000, 100, when=FIXED_NOW - timedelta(days=i))
    for i in range(3):
        _record(det, f"low{i}", 1000, 5, when=FIXED_NOW - timedelta(days=i))
    r = det.check()
    assert r["status"] == "alert"
    assert r["consecutive_low_posts"] >= 3


def test_zero_impressions_excluded_from_baseline(det: ShadowBanDetector) -> None:
    # 5 normal posts + 1 with 0 impressions shouldn't break baseline
    for i in range(1, 6):
        _record(det, f"post{i}", 1000, 50, when=FIXED_NOW - timedelta(days=i))
    _record(det, "zero", 0, 0, when=FIXED_NOW - timedelta(hours=1))
    r = det.check()
    assert r["status"] == "ok"
    assert r["data_points"] == 6


def test_median_basic() -> None:
    assert ShadowBanDetector._median([1, 2, 3, 4, 5]) == 3.0
    assert ShadowBanDetector._median([1, 2, 3, 4]) == 2.5
    assert ShadowBanDetector._median([]) == 0.0


def test_record_then_list(det: ShadowBanDetector) -> None:
    _record(det, "x", 100, 5)
    sigs = det.list_post_signals()
    assert len(sigs) == 1
    assert sigs[0].target == "x"
    assert sigs[0].rate == 0.05


def test_checked_at_present(det: ShadowBanDetector) -> None:
    r = det.check()
    assert "checked_at" in r
    assert r["checked_at"].startswith("20")  # ISO format year