"""Post actions: create and delete."""

from __future__ import annotations

import logging
import re
from typing import Any

from .client import BrowserClient, LINKEDIN_BASE

log = logging.getLogger("linkedin_mcp.browser.post")

MAX_TEXT_LENGTH = 3000
_URN_PATTERN = re.compile(r"^urn:li:(?:activity|share|ugcPost):\d+$")


def _validate_text(text: str) -> None:
    if not text or not text.strip():
        raise ValueError("text is required")
    if len(text) > MAX_TEXT_LENGTH:
        raise ValueError(f"text too long: {len(text)} > {MAX_TEXT_LENGTH}")


async def create_post(
    client: BrowserClient,
    text: str,
    media_url: str | None = None,
    visibility: str = "PUBLIC",
) -> dict[str, Any]:
    """Create a new LinkedIn post (text + optional media URL)."""
    _validate_text(text)
    if visibility not in ("PUBLIC", "CONNECTIONS"):
        raise ValueError(f"visibility must be PUBLIC or CONNECTIONS, got {visibility!r}")

    await client.navigate(f"{LINKEDIN_BASE}/feed/")
    tree = await client.snapshot()

    # The "Start a post" button is a button with name matching "Start a post"
    start_ref = _find_button_ref(tree, name_match=r"start a post")
    if not start_ref:
        return {"ok": False, "error": "no_start_post_button"}
    await client.click(start_ref)

    # The composer is a textbox
    composer_ref = _find_textbox_ref(await client.snapshot())
    if not composer_ref:
        return {"ok": False, "error": "no_composer_textbox"}
    await client.fill(composer_ref, text)

    # Post button
    post_ref = _find_button_ref(
        await client.snapshot(), name_match=r"^post$"
    )
    if not post_ref:
        return {"ok": False, "error": "no_post_button"}
    await client.click(post_ref)

    log.info("post created (visibility=%s, with_media=%s)", visibility, bool(media_url))
    # Note: media_url not yet wired (would need file chooser handling)
    return {"ok": True, "text_len": len(text), "visibility": visibility, "with_media": False}


async def delete_post(client: BrowserClient, post_urn: str) -> dict[str, Any]:
    """Delete a post by its URN."""
    if not _URN_PATTERN.match(post_urn):
        raise ValueError(f"Invalid post_urn: {post_urn!r}")
    # LinkedIn doesn't have a public URN→URL redirect without a session.
    # Use the activity feed and find the post by content. For v0.1, we require
    # the caller to be on a profile or feed where the post is visible.
    return {
        "ok": False,
        "error": "delete_post by URN not yet implemented — navigate to the post URL manually and use the UI",
    }


# ---------------------------------------------------------------------------
# Snapshot helpers
# ---------------------------------------------------------------------------


def _find_button_ref(tree: str, *, name_match: str = "") -> str | None:
    return _find_ref_in_snapshot(tree, role="button", name_match=name_match)


def _find_textbox_ref(tree: str) -> str | None:
    return _find_ref_in_snapshot(
        tree, role="textbox", name_match=r""
    )


def _find_ref_in_snapshot(
    tree: str, *, role: str, name_match: str = ""
) -> str | None:
    if not tree:
        return None
    name_re = re.compile(name_match, re.I) if name_match else None
    for line in tree.splitlines():
        s = line.lstrip("-* ").strip()
        if not s.lower().startswith(role):
            continue
        m_name = re.search(r'"([^"]*)"', s)
        if not m_name:
            continue
        name = m_name.group(1)
        if name_re and not name_re.search(name):
            continue
        m_ref = re.search(r"\[ref=([^\]]+)\]", s)
        if m_ref:
            return f"@{m_ref.group(1)}"
    return None
