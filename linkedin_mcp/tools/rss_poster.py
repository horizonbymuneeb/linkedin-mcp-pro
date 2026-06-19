"""MCP tools for RSS auto-posts (v0.6.0)."""

from __future__ import annotations

from typing import Any

from ..rss_poster import Feed, RSSPoster


def _poster() -> RSSPoster:
    return RSSPoster()


def list_rss_feeds() -> list[dict[str, Any]]:
    return [f.to_dict() for f in _poster().list_feeds()]


def add_rss_feed(
    name: str,
    url: str,
    template: str | None = None,
    text_prefix: str = "",
    max_per_day: int = 1,
) -> dict[str, Any]:
    feed = Feed(
        name=name,
        url=url,
        template=template,
        text_prefix=text_prefix,
        max_per_day=max_per_day,
    )
    _poster().add(feed)
    return {"ok": True, "feed": feed.to_dict()}


def remove_rss_feed(name: str) -> dict[str, Any]:
    if not _poster().remove(name):
        raise ValueError(f"Feed {name!r} not found")
    return {"ok": True, "removed": name}


def poll_rss_feeds(limit_per_feed: int = 3) -> dict[str, Any]:
    """Poll every enabled feed and return new items to post."""
    return _poster().poll(limit_per_feed=limit_per_feed)