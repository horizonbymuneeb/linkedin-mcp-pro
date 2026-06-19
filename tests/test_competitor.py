"""Tests for competitor monitor (v1.0.0)."""

from __future__ import annotations

from pathlib import Path

import pytest

from linkedin_mcp.competitor import Competitor, CompetitorError, CompetitorMonitor, CompetitorPost


@pytest.fixture
def mon(tmp_path: Path) -> CompetitorMonitor:
    return CompetitorMonitor(
        path=tmp_path / "c.yaml",
        posts_path=tmp_path / "p.yaml",
    )


def test_empty(mon: CompetitorMonitor) -> None:
    assert mon.list_competitors() == []
    assert mon.list_posts() == []


def test_add_competitor(mon: CompetitorMonitor) -> None:
    c = mon.add("Alice", "https://linkedin.com/in/alice")
    assert c.name == "Alice"


def test_add_duplicate_raises(mon: CompetitorMonitor) -> None:
    mon.add("A", "https://e.com")
    with pytest.raises(CompetitorError):
        mon.add("A", "https://e.com")


def test_add_requires_url(mon: CompetitorMonitor) -> None:
    with pytest.raises(CompetitorError):
        mon.add("A", "")


def test_add_requires_name(mon: CompetitorMonitor) -> None:
    with pytest.raises(CompetitorError):
        mon.add("", "https://e.com")


def test_remove(mon: CompetitorMonitor) -> None:
    mon.add("A", "https://e.com")
    assert mon.remove("A") is True
    assert mon.remove("A") is False


def test_add_post(mon: CompetitorMonitor) -> None:
    p = mon.add_post("Alice", "https://linkedin.com/posts/1", "Great post")
    assert p.competitor == "Alice"
    assert p.impressions == 0


def test_add_post_with_metrics(mon: CompetitorMonitor) -> None:
    p = mon.add_post(
        "Alice", "https://e.com/1", "Viral",
        impressions=10000, reactions=500, comments=80,
    )
    assert p.reactions == 500


def test_list_posts_filter_by_competitor(mon: CompetitorMonitor) -> None:
    mon.add_post("Alice", "https://e.com/1", "x")
    mon.add_post("Bob", "https://e.com/2", "y")
    mon.add_post("Alice", "https://e.com/3", "z")
    assert len(mon.list_posts()) == 3
    assert len(mon.list_posts(competitor="Alice")) == 2


def test_weekly_report_top_posts(mon: CompetitorMonitor) -> None:
    mon.add("Alice", "https://e.com")
    mon.add("Bob", "https://e.com")
    mon.add_post("Alice", "https://e.com/1", "low", reactions=10, comments=2)
    mon.add_post("Bob", "https://e.com/2", "high", reactions=500, comments=80)
    r = mon.weekly_report()
    assert r["total_posts_in_window"] == 2
    assert r["top_posts"][0]["title"] == "high"


def test_weekly_report_includes_tracked(mon: CompetitorMonitor) -> None:
    mon.add("Alice", "https://e.com")
    r = mon.weekly_report()
    assert "Alice" in r["tracked_competitors"]


def test_weekly_report_empty_data(mon: CompetitorMonitor) -> None:
    mon.add("Alice", "https://e.com")
    r = mon.weekly_report()
    assert r["total_posts_in_window"] == 0
    assert r["top_posts"] == []