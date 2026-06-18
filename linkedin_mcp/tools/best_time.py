"""MCP tool for best-time analyzer (v0.6.0)."""

from __future__ import annotations

from typing import Any

from ..best_time import BestTimeAnalyzer


def get_best_posting_times(days: int = 90) -> dict[str, Any]:
    """Recommend the best posting slots from your history.

    Returns a dict with by_hour, by_day_of_week, best_slots, data_points.
    """
    return BestTimeAnalyzer().recommend(days=days)