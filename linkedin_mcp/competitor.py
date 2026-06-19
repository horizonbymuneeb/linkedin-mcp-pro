"""Competitor monitor for linkedin-mcp-pro (v1.0.0).

Track a small list of competitor LinkedIn profiles. Polls their recent
posts and surfaces a weekly report with the top-engagement items.

This module is read-only over the local DB; the actual scraping is
expected to be done by your existing browser pipeline (we don't open
LinkedIn here — same ban-safety reasons).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import yaml


class CompetitorError(Exception):
    """Raised for any competitor-store failure."""


@dataclass
class CompetitorPost:
    competitor: str
    url: str
    title: str
    impressions: int = 0
    reactions: int = 0
    comments: int = 0
    posted_at: str = ""


@dataclass
class Competitor:
    name: str
    profile_url: str
    notes: str = ""


class CompetitorMonitor:
    """Track competitors + their posts."""

    def __init__(
        self,
        path: str | Path | None = None,
        posts_path: str | Path | None = None,
    ):
        self.path = Path(
            path
            or os.environ.get("LINKEDIN_MCP_COMPETITORS_FILE")
            or (Path.home() / ".linkedin-mcp" / "competitors.yaml")
        )
        self.posts_path = Path(
            posts_path
            or os.environ.get("LINKEDIN_MCP_COMPETITOR_POSTS_FILE")
            or (Path.home() / ".linkedin-mcp" / "competitor_posts.yaml")
        )

    # -- Competitors CRUD -----------------------------------------------

    def list_competitors(self) -> list[Competitor]:
        if not self.path.exists():
            return []
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {"competitors": []}
        except yaml.YAMLError as e:
            raise CompetitorError(f"Invalid YAML in {self.path}: {e}") from e
        return [
            Competitor(
                name=str(c.get("name", "")),
                profile_url=str(c.get("profile_url", "")),
                notes=str(c.get("notes", "")),
            )
            for c in data.get("competitors", []) or []
        ]

    def add(self, name: str, profile_url: str, notes: str = "") -> Competitor:
        if not name:
            raise CompetitorError("Competitor name is required")
        if not profile_url:
            raise CompetitorError(f"Competitor {name!r} needs a profile_url")
        current = self.list_competitors()
        if any(c.name == name for c in current):
            raise CompetitorError(f"Competitor {name!r} already tracked")
        comp = Competitor(name=name, profile_url=profile_url, notes=notes)
        current.append(comp)
        self._save_competitors(current)
        return comp

    def remove(self, name: str) -> bool:
        current = self.list_competitors()
        new = [c for c in current if c.name != name]
        if len(new) == len(current):
            return False
        self._save_competitors(new)
        return True

    def _save_competitors(self, competitors: list[Competitor]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(
                {"competitors": [
                    {"name": c.name, "profile_url": c.profile_url, "notes": c.notes}
                    for c in competitors
                ]},
                fh,
                sort_keys=False,
                allow_unicode=True,
                width=120,
            )

    # -- Posts ----------------------------------------------------------

    def list_posts(self, competitor: Optional[str] = None) -> list[CompetitorPost]:
        if not self.posts_path.exists():
            return []
        try:
            with self.posts_path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {"posts": []}
        except yaml.YAMLError as e:
            raise CompetitorError(f"Invalid YAML in {self.posts_path}: {e}") from e
        items = data.get("posts", []) or []
        if competitor is not None:
            items = [p for p in items if p.get("competitor") == competitor]
        return [
            CompetitorPost(
                competitor=str(p.get("competitor", "")),
                url=str(p.get("url", "")),
                title=str(p.get("title", "")),
                impressions=int(p.get("impressions", 0)),
                reactions=int(p.get("reactions", 0)),
                comments=int(p.get("comments", 0)),
                posted_at=str(p.get("posted_at", "")),
            )
            for p in items
        ]

    def add_post(
        self,
        competitor: str,
        url: str,
        title: str,
        impressions: int = 0,
        reactions: int = 0,
        comments: int = 0,
        posted_at: Optional[str] = None,
    ) -> CompetitorPost:
        if not competitor or not url:
            raise CompetitorError("competitor and url are required")
        post = CompetitorPost(
            competitor=competitor,
            url=url,
            title=title,
            impressions=impressions,
            reactions=reactions,
            comments=comments,
            posted_at=posted_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        current = self.list_posts()
        current.append(post)
        self.posts_path.parent.mkdir(parents=True, exist_ok=True)
        with self.posts_path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(
                {"posts": [
                    {
                        "competitor": p.competitor, "url": p.url, "title": p.title,
                        "impressions": p.impressions, "reactions": p.reactions,
                        "comments": p.comments, "posted_at": p.posted_at,
                    }
                    for p in current
                ]},
                fh,
                sort_keys=False,
                allow_unicode=True,
                width=120,
            )
        return post

    def weekly_report(self, days: int = 7) -> dict[str, Any]:
        """Return top-engagement posts across all competitors in last N days."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        posts = [p for p in self.list_posts() if p.posted_at >= cutoff]
        ranked = sorted(
            posts,
            key=lambda p: p.reactions * 5 + p.comments * 3 + p.impressions / 100,
            reverse=True,
        )[:10]
        return {
            "window_days": days,
            "tracked_competitors": [c.name for c in self.list_competitors()],
            "total_posts_in_window": len(posts),
            "top_posts": [
                {
                    "competitor": p.competitor,
                    "title": p.title[:80],
                    "url": p.url,
                    "reactions": p.reactions,
                    "comments": p.comments,
                    "impressions": p.impressions,
                    "posted_at": p.posted_at,
                }
                for p in ranked
            ],
        }