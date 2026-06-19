"""MCP tools for shadow-ban detection (v0.7.0)."""

from __future__ import annotations

from typing import Any

from ..shadowban import ShadowBanDetector


def _detector() -> ShadowBanDetector:
    return ShadowBanDetector()


def record_post_metrics(target: str, impressions: int, engagement: int) -> dict[str, Any]:
    _detector().record_post_metrics(target, impressions, engagement)
    return {"ok": True, "recorded": {"target": target, "impressions": impressions, "engagement": engagement}}


def check_shadowban(
    drop_threshold: float = 0.50,
    min_baseline_posts: int = 5,
) -> dict[str, Any]:
    """Run a shadow-ban check.

    Returns a dict with status (ok/warning/alert/no_data), data_points,
    baseline_engagement_rate, latest_engagement_rate, drop_pct,
    consecutive_low_posts, alerts, checked_at.
    """
    return _detector().check(
        drop_threshold=drop_threshold,
        min_baseline_posts=min_baseline_posts,
    )