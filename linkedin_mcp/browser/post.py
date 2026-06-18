"""Post actions: create (with optional media) and delete.

For media, the local file path is passed to the browser file chooser. We do
NOT host or proxy the file — agent-browser reads it directly from the path
provided.

For delete, we navigate to the post URL (or feed) and click the "Delete" or
"Remove" option in the post's overflow menu.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .client import BrowserClient, LINKEDIN_BASE

log = logging.getLogger("linkedin_mcp.browser.post")

MAX_TEXT_LENGTH = 3000
_URN_PATTERN = re.compile(r"^urn:li:(?:activity|share|ugcPost):\d+$")
_ALLOWED_MEDIA_EXT = {".jpg", ".jpeg", ".png", ".gif", ".mp4", ".mov"}
_MAX_MEDIA_BYTES = 200 * 1024 * 1024  # 200MB


def _validate_text(text: str) -> None:
    if not text or not text.strip():
        raise ValueError("text is required")
    if len(text) > MAX_TEXT_LENGTH:
        raise ValueError(f"text too long: {len(text)} > {MAX_TEXT_LENGTH}")


def _validate_visibility(visibility: str) -> str:
    if visibility not in ("PUBLIC", "CONNECTIONS"):
        raise ValueError(f"visibility must be PUBLIC or CONNECTIONS, got {visibility!r}")
    return visibility


def _validate_media_path(media_path: str) -> Path:
    p = Path(media_path).expanduser().resolve()
    if not p.exists():
        raise ValueError(f"media file not found: {media_path}")
    if not p.is_file():
        raise ValueError(f"media path is not a file: {media_path}")
    if p.suffix.lower() not in _ALLOWED_MEDIA_EXT:
        raise ValueError(
            f"unsupported media type {p.suffix!r}; allowed: {sorted(_ALLOWED_MEDIA_EXT)}"
        )
    if p.stat().st_size > _MAX_MEDIA_BYTES:
        raise ValueError(f"media file too large: {p.stat().st_size} > {_MAX_MEDIA_BYTES}")
    return p


def _validate_urn_or_url(target: str) -> str:
    if not target:
        raise ValueError("post_url or post_urn is required")
    if target.startswith("http://") or target.startswith("https://"):
        parsed = urlparse(target)
        if "linkedin.com" not in parsed.netloc:
            raise ValueError(f"URL must be on linkedin.com: {target!r}")
        return target
    if _URN_PATTERN.match(target):
        return f"{LINKEDIN_BASE}/feed/update/{target}/"
    raise ValueError(f"Invalid target: must be a LinkedIn URL or URN, got {target!r}")


# ---------------------------------------------------------------------------
# Public actions
# ---------------------------------------------------------------------------


async def create_post(
    client: BrowserClient,
    text: str,
    media_path: str | None = None,
    visibility: str = "PUBLIC",
) -> dict[str, Any]:
    """Create a new LinkedIn post (text + optional local media file).

    Args:
        text: Post body (max 3000 chars).
        media_path: Optional local path to image (.jpg/.png/.gif) or video (.mp4/.mov).
        visibility: PUBLIC or CONNECTIONS.

    Returns:
        {"ok": True, "text_len": int, "with_media": bool, "media_type": str|None}
    """
    _validate_text(text)
    visibility = _validate_visibility(visibility)

    media_file: Path | None = None
    media_type: str | None = None
    if media_path:
        media_file = _validate_media_path(media_path)
        media_type = "video" if media_file.suffix.lower() in {".mp4", ".mov"} else "image"

    await client.navigate(f"{LINKEDIN_BASE}/feed/")
    tree = await client.snapshot()

    # Open the post composer
    start_ref = _find_button_ref(tree, name_match=r"start a post")
    if not start_ref:
        return {"ok": False, "error": "no_start_post_button"}
    await client.click(start_ref)

    # Fill the composer textbox
    composer_ref = _find_textbox_ref(await client.snapshot())
    if not composer_ref:
        return {"ok": False, "error": "no_composer_textbox"}
    await client.fill(composer_ref, text)

    # Attach media if provided
    if media_file is not None:
        attached = await _attach_media(client, media_file, media_type)
        if not attached:
            return {"ok": False, "error": "media_attach_failed", "path": str(media_file)}

    # Click Post
    post_ref = _find_button_ref(await client.snapshot(), name_match=r"^post$")
    if not post_ref:
        return {"ok": False, "error": "no_post_button"}
    await client.click(post_ref)

    log.info(
        "post created (visibility=%s, with_media=%s, media_type=%s)",
        visibility, media_file is not None, media_type,
    )
    return {
        "ok": True,
        "text_len": len(text),
        "visibility": visibility,
        "with_media": media_file is not None,
        "media_type": media_type,
    }


async def delete_post(client: BrowserClient, target: str) -> dict[str, Any]:
    """Delete a post by URL or URN.

    Navigates to the post, opens the overflow menu, clicks Delete, confirms.
    """
    post_url = _validate_urn_or_url(target)

    await client.navigate(post_url)
    tree = await client.snapshot()

    # Open the post's "..." overflow menu
    overflow_ref = _find_button_ref(tree, name_match=r"^more|^more actions|^open actions")
    if not overflow_ref:
        return {"ok": False, "target": post_url, "error": "no_overflow_menu"}
    await client.click(overflow_ref)

    # Find Delete in the menu
    delete_ref = _find_button_ref(
        await client.snapshot(), name_match=r"^delete$|^remove$"
    )
    if not delete_ref:
        return {"ok": False, "target": post_url, "error": "no_delete_option"}
    await client.click(delete_ref)

    # Confirm modal — usually labeled "Delete" again
    confirm_ref = _find_button_ref(
        await client.snapshot(), name_match=r"^delete$"
    )
    if confirm_ref:
        await client.click(confirm_ref)

    log.info("post deleted: %s", post_url)
    return {"ok": True, "target": post_url}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _attach_media(client: BrowserClient, media_file: Path, media_type: str) -> bool:
    """Click the media icon, then upload the file via the file chooser."""
    # The media icon is usually a button labeled with photo/video icon.
    # LinkedIn exposes it as a button with an accessible name like "Add a photo"
    # or "Add a video" (depending on type).
    if media_type == "image":
        icon_match = r"photo|media|image"
    else:
        icon_match = r"video|media"

    tree = await client.snapshot()
    icon_ref = _find_button_ref(tree, name_match=icon_match)
    if not icon_ref:
        # Try a more generic approach: any button near the composer
        icon_ref = _find_button_ref(tree, name_match=r"^media$")
    if not icon_ref:
        log.warning("media icon not found")
        return False
    await client.click(icon_ref)

    # The file chooser is a hidden <input type="file">. agent-browser has a
    # command for this: `agent-browser upload @eN /path/to/file`. The ref is
    # the file input ref.
    upload_tree = await client.snapshot()
    file_input_ref = _find_file_input_ref(upload_tree)
    if not file_input_ref:
        log.warning("file input not found in DOM")
        return False

    try:
        await client.upload(file_input_ref, str(media_file))
    except Exception as exc:
        log.error("file upload failed: %s", exc)
        return False

    return True


# ---------------------------------------------------------------------------
# Snapshot parsing helpers
# ---------------------------------------------------------------------------


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


def _find_textbox_ref(tree: str) -> str | None:
    if not tree:
        return None
    for line in tree.splitlines():
        s = line.lstrip("-* \t").strip()
        if not s.lower().startswith("textbox"):
            continue
        m_ref = re.search(r"\[ref=([^\]]+)\]", s)
        if m_ref:
            return f"@{m_ref.group(1)}"
    return None


def _find_file_input_ref(tree: str) -> str | None:
    """LinkedIn's file input is usually a generic input element."""
    if not tree:
        return None
    for line in tree.splitlines():
        s = line.lstrip("-* \t").strip()
        # The file chooser input has a generic role
        if "file" in s.lower() or "input" in s.lower():
            m_ref = re.search(r"\[ref=([^\]]+)\]", s)
            if m_ref:
                return f"@{m_ref.group(1)}"
    return None
