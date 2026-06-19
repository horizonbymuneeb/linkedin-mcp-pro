"""MCP tools for competitor monitor (v1.0.0)."""

from __future__ import annotations

from typing import Any

from ..competitor import CompetitorMonitor


def list_competitors() -> list[dict[str, Any]]:
    return [
        {"name": c.name, "profile_url": c.profile_url, "notes": c.notes}
        for c in CompetitorMonitor().list_competitors()
    ]


def add_competitor(name: str, profile_url: str, notes: str = "") -> dict[str, Any]:
    c = CompetitorMonitor().add(name, profile_url, notes)
    return {"ok": True, "competitor": {"name": c.name, "profile_url": c.profile_url, "notes": c.notes}}


def remove_competitor(name: str) -> dict[str, Any]:
    if not CompetitorMonitor().remove(name):
        raise ValueError(f"Competitor {name!r} not found")
    return {"ok": True, "removed": name}


def add_competitor_post(
    competitor: str, url: str, title: str,
    impressions: int = 0, reactions: int = 0, comments: int = 0,
) -> dict[str, Any]:
    p = CompetitorMonitor().add_post(
        competitor, url, title,
        impressions=impressions, reactions=reactions, comments=comments,
    )
    return {"ok": True, "post": {
        "competitor": p.competitor, "url": p.url, "title": p.title,
        "impressions": p.impressions, "reactions": p.reactions,
        "comments": p.comments, "posted_at": p.posted_at,
    }}


def get_competitor_weekly_report(days: int = 7) -> dict[str, Any]:
    return CompetitorMonitor().weekly_report(days=days)