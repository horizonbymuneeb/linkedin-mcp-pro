"""RSS auto-posts for linkedin-mcp-pro (v0.6.0).

Poll RSS feeds and post new entries as LinkedIn posts. Each feed has
an optional template + post schedule. Already-seen GUIDs are stored
in a JSON sidecar so we never re-post an item.

Uses stdlib ``xml.etree.ElementTree`` (no feedparser dep).
"""

from __future__ import annotations

import json
import os
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml


class RSSError(Exception):
    """Raised for any RSS-poster failure."""


@dataclass
class Feed:
    """A single RSS feed configuration."""

    name: str
    url: str
    template: Optional[str] = None
    text_prefix: str = ""
    days: list[str] = field(default_factory=list)
    time: Optional[str] = None
    max_per_day: int = 1
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "url": self.url,
            "enabled": self.enabled,
        }
        if self.template:
            d["template"] = self.template
        if self.text_prefix:
            d["text_prefix"] = self.text_prefix
        if self.days:
            d["days"] = list(self.days)
        if self.time:
            d["time"] = self.time
        if self.max_per_day != 1:
            d["max_per_day"] = self.max_per_day
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Feed":
        if not data.get("name"):
            raise RSSError("Feed needs a name")
        if not data.get("url"):
            raise RSSError(f"Feed {data.get('name')!r} needs a url")
        return cls(
            name=str(data["name"]).strip(),
            url=str(data["url"]).strip(),
            template=data.get("template"),
            text_prefix=str(data.get("text_prefix", "")),
            days=[str(d).lower() for d in (data.get("days", []) or [])],
            time=data.get("time"),
            max_per_day=int(data.get("max_per_day", 1)),
            enabled=bool(data.get("enabled", True)),
        )

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise RSSError("Feed needs a name")
        if not self.url or not self.url.strip():
            raise RSSError(f"Feed {self.name!r} needs a url")


class RSSPoster:
    """Poll RSS feeds and produce new post texts."""

    def __init__(
        self,
        feeds_path: str | Path | None = None,
        seen_path: str | Path | None = None,
        http_timeout: float = 10.0,
    ):
        self.feeds_path = Path(
            feeds_path
            or os.environ.get("LINKEDIN_MCP_RSS_FEEDS_FILE")
            or (Path.home() / ".linkedin-mcp" / "feeds.yaml")
        )
        self.seen_path = Path(
            seen_path
            or os.environ.get("LINKEDIN_MCP_RSS_SEEN_FILE")
            or (Path.home() / ".linkedin-mcp" / "feeds_seen.json")
        )
        self.http_timeout = http_timeout

    # -- Feeds CRUD -----------------------------------------------------

    def list_feeds(self) -> list[Feed]:
        if not self.feeds_path.exists():
            return []
        try:
            with self.feeds_path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {"feeds": []}
        except yaml.YAMLError as e:
            raise RSSError(f"Invalid YAML in {self.feeds_path}: {e}") from e
        return [Feed.from_dict(f) for f in data.get("feeds", []) or []]

    def get(self, name: str) -> Feed:
        for f in self.list_feeds():
            if f.name == name:
                return f
        raise RSSError(f"Feed {name!r} not found")

    def save_all(self, feeds: list[Feed]) -> None:
        self.feeds_path.parent.mkdir(parents=True, exist_ok=True)
        with self.feeds_path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(
                {"feeds": [f.to_dict() for f in feeds]},
                fh,
                sort_keys=False,
                allow_unicode=True,
                width=120,
            )

    def add(self, feed: Feed) -> None:
        current = self.list_feeds()
        if any(f.name == feed.name for f in current):
            raise RSSError(f"Feed {feed.name!r} already registered")
        current.append(feed)
        self.save_all(current)

    def remove(self, name: str) -> bool:
        current = self.list_feeds()
        new = [f for f in current if f.name != name]
        if len(new) == len(current):
            return False
        self.save_all(new)
        return True

    # -- Seen-GUID tracking ---------------------------------------------

    def _load_seen(self) -> dict[str, list[str]]:
        if not self.seen_path.exists():
            return {}
        try:
            with self.seen_path.open("r", encoding="utf-8") as fh:
                return json.load(fh) or {}
        except json.JSONDecodeError:
            return {}

    def _save_seen(self, data: dict[str, list[str]]) -> None:
        self.seen_path.parent.mkdir(parents=True, exist_ok=True)
        with self.seen_path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)

    # -- Fetch + parse --------------------------------------------------

    def _fetch(self, url: str) -> bytes:
        req = urllib.request.Request(
            url, headers={"User-Agent": "linkedin-mcp-pro/0.6 RSS poller"}
        )
        with urllib.request.urlopen(req, timeout=self.http_timeout) as resp:
            return resp.read()

    @staticmethod
    def _parse_feed(xml_bytes: bytes) -> list[dict[str, str]]:
        """Return list of {'guid', 'title', 'link', 'description'} items."""
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as e:
            raise RSSError(f"Invalid XML: {e}") from e
        # RSS 2.0: <rss><channel><item>
        # Atom:    <feed><entry>
        items: list[dict[str, str]] = []
        if root.tag == "rss":
            for channel in root.findall("channel"):
                for item in channel.findall("item"):
                    items.append(
                        {
                            "guid": (item.findtext("guid") or item.findtext("link") or "").strip(),
                            "title": (item.findtext("title") or "").strip(),
                            "link": (item.findtext("link") or "").strip(),
                            "description": (item.findtext("description") or "").strip(),
                        }
                    )
        elif root.tag.endswith("feed"):  # Atom namespace
            ns = "{http://www.w3.org/2005/Atom}"
            for entry in root.findall(f"{ns}entry"):
                items.append(
                    {
                        "guid": (entry.findtext(f"{ns}id") or entry.findtext(f"{ns}link") or "").strip(),
                        "title": (entry.findtext(f"{ns}title") or "").strip(),
                        "link": (entry.findtext(f"{ns}link") or "").strip(),
                        "description": (entry.findtext(f"{ns}summary") or entry.findtext(f"{ns}content") or "").strip(),
                    }
                )
        return items

    # -- High-level -----------------------------------------------------

    def _format_post(self, feed: Feed, item: dict[str, str]) -> str:
        """Render an RSS item as a LinkedIn post body."""
        parts: list[str] = []
        if feed.text_prefix:
            parts.append(feed.text_prefix.strip())
        if item.get("title"):
            parts.append(item["title"])
        if item.get("link"):
            parts.append(item["link"])
        return "\n\n".join(parts).strip()

    def poll(
        self,
        *,
        now: datetime | None = None,
        limit_per_feed: int = 3,
    ) -> dict[str, Any]:
        """Poll every enabled feed, return new items to post.

        Returns {"new_posts": [{"feed": str, "guid": str, "text": str}, ...], "feeds_polled": int}
        """
        now = now or datetime.now(timezone.utc)
        seen = self._load_seen()
        new_posts: list[dict[str, str]] = []
        feeds_polled = 0
        for feed in self.list_feeds():
            if not feed.enabled:
                continue
            feeds_polled += 1
            try:
                xml_bytes = self._fetch(feed.url)
                items = self._parse_feed(xml_bytes)
            except Exception:
                # Skip feeds that fail to fetch/parse this round
                continue
            seen_for_feed = set(seen.get(feed.name, []))
            added = 0
            for item in items:
                if added >= limit_per_feed:
                    break
                guid = item.get("guid") or item.get("link")
                if not guid:
                    continue
                if guid in seen_for_feed:
                    continue
                text = self._format_post(feed, item)
                if not text:
                    continue
                new_posts.append({"feed": feed.name, "guid": guid, "text": text})
                seen_for_feed.add(guid)
                added += 1
            seen[feed.name] = sorted(seen_for_feed)
        self._save_seen(seen)
        return {"new_posts": new_posts, "feeds_polled": feeds_polled}