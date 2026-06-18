"""Direct message actions."""

from __future__ import annotations

import logging
import re
from typing import Any

from .client import BrowserClient, LINKEDIN_BASE

log = logging.getLogger("linkedin_mcp.browser.message")

MAX_TEXT_LENGTH = 8000
_VALID_PUBLIC_ID = re.compile(r"^[a-zA-Z0-9\-_.]{3,100}$")


def _validate_public_id(public_id: str) -> None:
    if not public_id or not _VALID_PUBLIC_ID.match(public_id):
        raise ValueError(f"Invalid public_id {public_id!r}")


async def send_message(
    client: BrowserClient,
    public_id: str,
    text: str,
) -> dict[str, Any]:
    """Send a direct message to a 1st-degree connection."""
    _validate_public_id(public_id)
    if not text or not text.strip():
        raise ValueError("text is required")
    if len(text) > MAX_TEXT_LENGTH:
        raise ValueError(f"text too long: {len(text)} > {MAX_TEXT_LENGTH}")

    # Try the messaging deeplink first (faster, no navigation needed)
    await client.navigate(f"{LINKEDIN_BASE}/messaging/compose/?recipient={public_id}")

    tree = await client.snapshot()
    composer_ref = _find_textbox_ref(tree)
    if not composer_ref:
        return {"ok": False, "target": public_id, "error": "no_message_composer"}

    await client.fill(composer_ref, text)
    send_ref = _find_button_ref(await client.snapshot(), name_match=r"^send$")
    if not send_ref:
        return {"ok": False, "target": public_id, "error": "no_send_button"}
    await client.click(send_ref)

    log.info("message sent to %s (len=%d)", public_id, len(text))
    return {"ok": True, "target": public_id, "len": len(text)}


def _find_textbox_ref(tree: str) -> str | None:
    if not tree:
        return None
    for line in tree.splitlines():
        s = line.lstrip("-* ").strip()
        if not s.lower().startswith("textbox"):
            continue
        m_ref = re.search(r"\[ref=([^\]]+)\]", s)
        if m_ref:
            return f"@{m_ref.group(1)}"
    return None


def _find_button_ref(tree: str, *, name_match: str = "") -> str | None:
    if not tree:
        return None
    name_re = re.compile(name_match, re.I) if name_match else None
    for line in tree.splitlines():
        s = line.lstrip("-* ").strip()
        if not s.lower().startswith("button"):
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
