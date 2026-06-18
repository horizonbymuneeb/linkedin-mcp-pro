"""MCP tools for post analytics (v0.6.0).

Seven read-only tools, all backed by ``Analytics`` in
``linkedin_mcp.analytics``. None of these touch LinkedIn — they're
purely local DB reads, so they bypass the SafetyGuard (same pattern as
the templates / deadman / scheduler tools).

Tools:
    get_post_volume(days=30)        -> dict {date: count}
    get_post_success_rate(days=30)  -> dict {total, success, failed, ...}
    get_quota_usage()               -> dict {day, actions[], total}
    get_top_posting_hours(days=90)  -> dict {hour: count}
    get_top_posting_days(days=90)   -> dict {weekday: count}
    get_recent_posts(limit=10)      -> list[dict]
    get_analytics_summary(days=30)  -> dict
"""

from __future__ import annotations

from typing import Any

from ..analytics import Analytics
from ..config import load_config
from ..db import DB


def _analytics() -> Analytics:
    """Build an Analytics bound to a fresh DB at the configured path.

    The DB instance is owned by this call and closed when the Analytics
    object is garbage-collected. Reading is cheap; the file lock is
    released on close.
    """
    cfg = load_config()
    db = DB(cfg.storage.db_path)
    return Analytics(db)


def get_post_volume(days: int = 30) -> dict[str, int]:
    """Per-day count of post audits in the last ``days`` days (UTC)."""
    return _analytics().post_volume(days=days)


def get_post_success_rate(days: int = 30) -> dict[str, Any]:
    """Roll-up of post outcomes in the last ``days`` days."""
    return _analytics().post_success_rate(days=days)


def get_quota_usage() -> dict[str, Any]:
    """Today's per-action-type quota usage (raw, no caps)."""
    return _analytics().quota_usage()


def get_top_posting_hours(days: int = 90) -> dict[int, int]:
    """Distribution of post audits by hour-of-day (0..23, UTC)."""
    return _analytics().top_posting_hours(days=days)


def get_top_posting_days(days: int = 90) -> dict[str, int]:
    """Distribution of post audits by weekday name (Monday..Sunday)."""
    return _analytics().top_posting_days(days=days)


def get_recent_posts(limit: int = 10) -> list[dict[str, Any]]:
    """Most recent ``post`` audit rows, newest first."""
    return _analytics().recent_posts(limit=limit)


def get_analytics_summary(days: int = 30) -> dict[str, Any]:
    """One-call roll-up of the most useful analytics metrics."""
    return _analytics().summary(days=days)


__all__ = [
    "get_post_volume",
    "get_post_success_rate",
    "get_quota_usage",
    "get_top_posting_hours",
    "get_top_posting_days",
    "get_recent_posts",
    "get_analytics_summary",
]
