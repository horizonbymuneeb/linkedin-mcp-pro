"""Engagement actions: comment and react."""

from __future__ import annotations

import logging
import re
from typing import Any

from .client import BrowserClient, LINKEDIN_BASE

log = logging.getLogger("linkedin_mcp.browser.engage")

VALID_REACTIONS = ("LIKE", "CELEBRATE", "INSIGHTFUL", "LOVE", "SUPPORT", "FUNNY")
MAX_COMMENT_LENGTH = 1250
_URN_PATTERN = re.compile(r"^urn:li:(?:activity|share|ugcPost):\d+$")


def _validate_urn(urn: str) -> None:
    if not _URN_PATTERN.match(urn):
        raise ValueError(f"Invalid post_urn: {urn!r}")


async def comment_on_post(
    client: BrowserClient, post_urn: str, text: str
) -> dict[str, Any]:
    """Post a comment on a LinkedIn post."""
    _validate_urn(post_urn)
    if not text or not text.strip():
        raise ValueError("text is required")
    if len(text) > MAX_COMMENT_LENGTH:
        raise ValueError(f"text too long: {len(text)} > {MAX_COMMENT_LENGTH}")

    # We need the post URL. The URN alone doesn't map 1:1 to a feed URL.
    # For v0.1, we surface a clearer error so the caller can navigate.
    return {
        "ok": False,
        "error": (
            "comment_on_post requires a feed URL not a URN. "
            "Use the post URL from get_feed() output and add post_url param "
            "(TODO: extend schema in v0.2)."
        ),
    }


async def react_to_post(
    client: BrowserClient,
    post_urn: str,
    reaction_type: str = "LIKE",
) -> dict[str, Any]:
    """Add a reaction to a post. Same URL requirement as comment_on_post."""
    _validate_urn(post_urn)
    if reaction_type not in VALID_REACTIONS:
        raise ValueError(
            f"reaction_type must be one of {VALID_REACTIONS}, got {reaction_type!r}"
        )
    return {
        "ok": False,
        "error": "react_to_post requires a feed URL, not URN (TODO: extend schema in v0.2).",
    }
