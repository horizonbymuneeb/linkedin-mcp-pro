"""MCP tools for v1.0.0 features (multi-platform, coach, calendar, leads, competitor)."""

from __future__ import annotations

from typing import Any

from ..calendar import ContentCalendar, Entry
from ..competitor import CompetitorMonitor
from ..coach import EngagementCoach
from ..leads import Lead, LeadScraper
from ..multi_platform import cross_post, list_platforms, Post


# ----- Multi-platform -----


def list_platforms_tool() -> dict[str, Any]:
    return {"platforms": list_platforms()}


def cross_post_tool(text: str, platforms: list[str], link: str = "") -> dict[str, Any]:
    p = Post(text=text, link=link or None)
    return {"results": cross_post(p, platforms)}


# ----- Coach -----


def get_coaching_report(days: int = 30) -> dict[str, Any]:
    return EngagementCoach().report(days=days)


# ----- Calendar -----


def _cal() -> ContentCalendar:
    return ContentCalendar()


def list_calendar_entries(month: str = "", status: str = "") -> list[dict[str, Any]]:
    return [e.to_dict() for e in _cal().list_entries(month=month or None, status=status or None)]


def add_calendar_entry(
    date: str, title: str, body: str = "", status: str = "idea", tags: list[str] | None = None,
) -> dict[str, Any]:
    return {"ok": True, "entry": _cal().add(
        Entry(date=date, title=title, body=body, status=status, tags=tags or [])
    ).to_dict()}


def update_calendar_status(date: str, title: str, new_status: str) -> dict[str, Any]:
    return {"ok": True, "entry": _cal().update_status(date, title, new_status).to_dict()}


def get_calendar_summary(month: str) -> dict[str, Any]:
    return _cal().month_summary(month)


# ----- Leads -----


def _ls() -> LeadScraper:
    return LeadScraper()


def list_leads() -> list[dict[str, Any]]:
    out = []
    for l in _ls().list_leads():
        out.append({
            "name": l.name, "profile_url": l.profile_url,
            "title": l.title, "company": l.company,
            "location": l.location, "tags": l.tags, "notes": l.notes,
        })
    return out


def add_lead(
    name: str, profile_url: str,
    title: str = "", company: str = "", location: str = "",
    tags: list[str] | None = None, notes: str = "",
) -> dict[str, Any]:
    return {"ok": True, "lead": _ls().add(Lead(
        name=name, profile_url=profile_url, title=title,
        company=company, location=location, tags=tags or [], notes=notes,
    ))}


def export_leads_csv(tag: str = "", company: str = "") -> dict[str, Any]:
    items = _ls().filter(tag=tag or None, company=company or None)
    return {"count": len(items), "csv": _ls().to_csv(items)}


# ----- Competitor -----


def _mon() -> CompetitorMonitor:
    return CompetitorMonitor()


def list_competitors_tool() -> list[dict[str, Any]]:
    return [{"name": c.name, "profile_url": c.profile_url, "notes": c.notes}
            for c in _mon().list_competitors()]


def add_competitor_tool(name: str, profile_url: str, notes: str = "") -> dict[str, Any]:
    c = _mon().add(name, profile_url, notes)
    return {"ok": True, "competitor": {"name": c.name, "profile_url": c.profile_url, "notes": c.notes}}


def add_competitor_post_tool(
    competitor: str, url: str, title: str,
    impressions: int = 0, reactions: int = 0, comments: int = 0,
) -> dict[str, Any]:
    p = _mon().add_post(competitor, url, title, impressions=impressions, reactions=reactions, comments=comments)
    return {"ok": True, "post": {
        "competitor": p.competitor, "url": p.url, "title": p.title,
        "impressions": p.impressions, "reactions": p.reactions, "comments": p.comments,
        "posted_at": p.posted_at,
    }}


def get_competitor_report_tool(days: int = 7) -> dict[str, Any]:
    return _mon().weekly_report(days=days)