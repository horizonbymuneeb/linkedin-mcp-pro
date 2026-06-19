"""Multi-platform adapters for linkedin-mcp-pro (v1.0.0).

Basic adapters for cross-posting to other networks. Each adapter is
intentionally minimal — they translate a LinkedIn-shaped post into
the network's format and call a stub ``post()`` function that the
operator wires up (we don't ship real OAuth integrations for these
networks; the patterns are demonstrated so you can plug in your own
credentials).

Supported (scaffolded):
  - twitter / x
  - threads
  - bluesky
  - mastodon

These are STUBS — they don't actually post. They're a contract
showing how a real adapter would be wired in.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional


PLATFORMS = ("linkedin", "twitter", "threads", "bluesky", "mastodon")


class MultiPlatformError(Exception):
    """Raised for any multi-platform failure."""


@dataclass
class Post:
    """A platform-agnostic post body."""

    text: str
    link: Optional[str] = None
    image_url: Optional[str] = None


class BaseAdapter:
    """Override ``format()`` and ``post()`` in subclasses."""

    name: str = "base"
    char_limit: int = 3000

    def format(self, post: Post) -> str:
        """Translate a Post into this platform's wire format."""
        text = post.text.strip()
        if len(text) > self.char_limit:
            text = text[: self.char_limit - 1] + "…"
        if post.link:
            text += f"\n\n{post.link}"
        return text

    def post(self, formatted: str) -> dict[str, Any]:
        """Stub — would call platform API. Returns the would-be post payload."""
        return {
            "platform": self.name,
            "would_post": formatted,
            "stub": True,
            "note": (
                "This adapter is a scaffold. Wire your own credentials "
                "and API call here to actually post."
            ),
        }


class TwitterAdapter(BaseAdapter):
    name = "twitter"
    char_limit = 280

    def format(self, post: Post) -> str:
        text = super().format(post)
        # Twitter strips newlines in some clients
        return " ".join(text.splitlines())


class ThreadsAdapter(BaseAdapter):
    name = "threads"
    char_limit = 500


class BlueskyAdapter(BaseAdapter):
    name = "bluesky"
    char_limit = 300


class MastodonAdapter(BaseAdapter):
    name = "mastodon"
    char_limit = 500


_ADAPTERS: dict[str, BaseAdapter] = {
    "twitter": TwitterAdapter(),
    "threads": ThreadsAdapter(),
    "bluesky": BlueskyAdapter(),
    "mastodon": MastodonAdapter(),
}


def get_adapter(platform: str) -> BaseAdapter:
    if platform not in _ADAPTERS:
        raise MultiPlatformError(
            f"Unknown platform {platform!r}; valid: {list(_ADAPTERS)}"
        )
    return _ADAPTERS[platform]


def cross_post(post: Post, platforms: list[str]) -> dict[str, Any]:
    """Format the post for each platform and stub-call post().

    Returns a dict {platform: result_dict}.
    """
    if not platforms:
        raise MultiPlatformError("No platforms specified")
    out: dict[str, Any] = {}
    for p in platforms:
        adapter = get_adapter(p)
        formatted = adapter.format(post)
        out[p] = adapter.post(formatted)
    return out


def list_platforms() -> list[dict[str, Any]]:
    return [
        {"name": a.name, "char_limit": a.char_limit}
        for a in _ADAPTERS.values()
    ]