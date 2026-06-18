"""Engagement actions: comment and react.

Both actions require a post URL (not a URN) because LinkedIn's browser UI
navigates by URL, not by internal identifier. The Voyager API returns URNs
from /feed/updates; the URL can usually be derived as:

    https://www.linkedin.com/feed/update/urn:li:activity:1234567890/

If the caller already has the URL, pass it directly.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal
from urllib.parse import urlparse

from .client import BrowserClient, LINKEDIN_BASE

log = logging.getLogger("linkedin_mcp.browser.engage")

VALID_REACTIONS = ("LIKE", "CELEBRATE", "INSIGHTFUL", "LOVE", "SUPPORT", "FUNNY", "CURIOUS", "MIND")
MAX_COMMENT_LENGTH = 1250
_URN_PATTERN = re.compile(r"^urn:li:(?:activity|share|ugcPost):\d+$")
_REACTION_LABEL = {
    "LIKE": "Like",
    "CELEBRATE": "Celebrate",
    "INSIGHTFUL": "Insightful",
    "LOVE": "Love",
    "SUPPORT": "Support",
    "FUNNY": "Funny",
    "CURIOUS": "Curious",
    "MIND": "Mind blown",
}


def _validate_urn_or_url(target: str) -> str:
    """Accept either a URN or a full LinkedIn post URL. Return a navigable URL."""
    if not target:
        raise ValueError("post_url or post_urn is required")

    if target.startswith("http://") or target.startswith("https://"):
        parsed = urlparse(target)
        if "linkedin.com" not in parsed.netloc:
            raise ValueError(f"URL must be on linkedin.com: {target!r}")
        return target

    if _URN_PATTERN.match(target):
        # Convert URN to LinkedIn feed URL
        # urn:li:activity:12345 -> https://www.linkedin.com/feed/update/urn:li:activity:12345/
        return f"{LINKEDIN_BASE}/feed/update/{target}/"

    raise ValueError(
        f"Invalid target: must be a LinkedIn URL or URN, got {target!r}"
    )


def _validate_reaction(reaction_type: str) -> str:
    rt = reaction_type.upper()
    if rt not in VALID_REACTIONS:
        raise ValueError(
            f"reaction_type must be one of {VALID_REACTIONS}, got {reaction_type!r}"
        )
    return rt


# ---------------------------------------------------------------------------
# Public actions
# ---------------------------------------------------------------------------


async def comment_on_post(
    client: BrowserClient,
    target: str,
    text: str,
) -> dict[str, Any]:
    """Post a comment on a LinkedIn post.

    Args:
        target: Either a full LinkedIn post URL OR a URN (urn:li:activity:...).
        text: Comment text (max 1250 chars).

    Returns:
        {"ok": True, "target": url, "len": int}
    """
    post_url = _validate_urn_or_url(target)
    if not text or not text.strip():
        raise ValueError("text is required")
    if len(text) > MAX_COMMENT_LENGTH:
        raise ValueError(f"text too long: {len(text)} > {MAX_COMMENT_LENGTH}")

    await client.navigate(post_url)
    tree = await client.snapshot()

    # Find the comment textbox — usually labeled "Add a comment" or just a textbox
    comment_ref = _find_textbox_ref(tree, name_match=r"comment")
    if not comment_ref:
        # Fallback: any visible textbox
        comment_ref = _find_textbox_ref(tree, name_match=r"")
    if not comment_ref:
        return {"ok": False, "target": post_url, "error": "no_comment_textbox"}

    await client.fill(comment_ref, text)

    # Find Post / Submit button
    post_ref = _find_button_ref(
        await client.snapshot(), name_match=r"^post$|^comment$|^submit$"
    )
    if not post_ref:
        return {"ok": False, "target": post_url, "error": "no_post_button"}

    await client.click(post_ref)
    log.info("comment posted on %s (len=%d)", post_url, len(text))
    return {"ok": True, "target": post_url, "len": len(text)}


async def react_to_post(
    client: BrowserClient,
    target: str,
    reaction_type: str = "LIKE",
) -> dict[str, Any]:
    """Add a reaction to a LinkedIn post.

    Args:
        target: Either a full LinkedIn post URL OR a URN.
        reaction_type: One of VALID_REACTIONS (default LIKE).

    Returns:
        {"ok": True, "target": url, "reaction": str}
    """
    post_url = _validate_urn_or_url(target)
    rt = _validate_reaction(reaction_type)
    label = _REACTION_LABEL[rt]

    await client.navigate(post_url)
    tree = await client.snapshot()

    # Click the Like button first (this opens a reactions popup in LinkedIn)
    like_ref = _find_button_ref(tree, name_match=r"^like$|^react$")
    if not like_ref:
        return {"ok": False, "target": post_url, "error": "no_like_button"}

    await client.click(like_ref)

    # The reactions popup may take a moment. For simple LIKE, the click above
    # already registered the reaction. For other types, we need to find the
    # specific reaction button in the popup.
    if rt != "LIKE":
        popup_tree = await client.snapshot()
        reaction_ref = _find_button_ref(
            popup_tree, name_match=re.escape(label)
        )
        if not reaction_ref:
            return {
                "ok": False,
                "target": post_url,
                "error": f"no_{rt.lower()}_button_in_popup",
            }
        await client.click(reaction_ref)

    log.info("reaction %s added on %s", rt, post_url)
    return {"ok": True, "target": post_url, "reaction": rt}


# ---------------------------------------------------------------------------
# Snapshot parsing helpers
# ---------------------------------------------------------------------------


def _find_textbox_ref(tree: str, name_match: str = "") -> str | None:
    if not tree:
        return None
    name_re = re.compile(name_match, re.I) if name_match else None
    for line in tree.splitlines():
        s = line.lstrip("-* \t").strip()
        if not s.lower().startswith("textbox"):
            continue
        m_ref = re.search(r"\[ref=([^\]]+)\]", s)
        if not m_ref:
            continue
        if name_re:
            m_name = re.search(r'"([^"]*)"', s)
            if m_name and not name_re.search(m_name.group(1)):
                continue
        return f"@{m_ref.group(1)}"
    return None


def _find_button_ref(tree: str, *, name_match: str = "") -> str | None:
    if not tree:
        return None
    name_re = re.compile(name_match, re.I) if name_match else None
    for line in tree.splitlines():
        s = line.lstrip("-* \t").strip()
        if not s.lower().startswith("button"):
            continue
        m_name = re.search(r'"([^"]*)"', s)
        if not m_name:
            continue
        if name_re and not name_re.search(m_name.group(1)):
            continue
        m_ref = re.search(r"\[ref=([^\]]+)\]", s)
        if m_ref:
            return f"@{m_ref.group(1)}"
    return None
