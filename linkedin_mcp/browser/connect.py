"""Connection-related actions: send, accept, decline, withdraw.

All actions navigate to the target's profile, find the Connect button via
the agent-browser snapshot, and click it. Personalized notes go through a
second "Add a note" dialog.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .client import BrowserClient, BrowserError, LINKEDIN_BASE

log = logging.getLogger("linkedin_mcp.browser.connect")

MAX_NOTE_LENGTH = 300
_VALID_PUBLIC_ID = re.compile(r"^[a-zA-Z0-9\-_.]{3,100}$")


def _validate_public_id(public_id: str) -> None:
    if not public_id or not _VALID_PUBLIC_ID.match(public_id):
        raise ValueError(
            f"Invalid public_id {public_id!r}: must be 3-100 chars, "
            f"alphanumeric + - _ ."
        )


def _validate_note(note: str) -> None:
    if len(note) > MAX_NOTE_LENGTH:
        raise ValueError(f"Note too long: {len(note)} > {MAX_NOTE_LENGTH}")


# ---------------------------------------------------------------------------
# Public actions
# ---------------------------------------------------------------------------


async def send_connection_request(
    client: BrowserClient,
    public_id: str,
    note: str = "",
) -> dict[str, Any]:
    """Send a connection request to a LinkedIn member.

    Returns ``{"ok": True, "target": public_id, "with_note": bool}``.
    Raises ``BrowserError`` on UI / network failure.
    """
    _validate_public_id(public_id)
    _validate_note(note)

    await client.navigate(f"{LINKEDIN_BASE}/in/{public_id}")

    # Snapshot to find the Connect button. The ref varies per page render.
    tree = await client.snapshot()

    # The Connect button typically has accessible name "Connect" or "Invite <name> to connect"
    # agent-browser's snapshot returns refs like [ref=e15] in the description.
    connect_ref = _find_ref_in_snapshot(tree, name_match=r"^connect$|invite.*to connect", role="button")
    if not connect_ref:
        # Maybe already connected / pending — detect and report
        if "pending" in tree.lower() or "withdraw" in tree.lower():
            return {"ok": False, "target": public_id, "error": "already_pending"}
        if "message" in tree.lower() and "connect" not in tree.lower():
            return {"ok": False, "target": public_id, "error": "already_connected"}
        return {"ok": False, "target": public_id, "error": "no_connect_button"}

    await client.click(connect_ref)

    # Note dialog (only if note provided and dialog appears)
    if note:
        add_note_ref = _find_ref_in_snapshot(
            await client.snapshot(), name_match=r"add a note", role="button"
        )
        if add_note_ref:
            await client.click(add_note_ref)
            # The note input is a textarea
            note_ref = _find_ref_in_snapshot(
                await client.snapshot(), name_match=r"", role="textbox"
            )
            if note_ref:
                await client.fill(note_ref, note)

    # Send the request
    send_ref = _find_ref_in_snapshot(
        await client.snapshot(), name_match=r"^send$|^send invitation$", role="button"
    )
    if not send_ref:
        return {"ok": False, "target": public_id, "error": "no_send_button"}

    await client.click(send_ref)
    log.info("connection request sent to %s (with_note=%s)", public_id, bool(note))
    return {"ok": True, "target": public_id, "with_note": bool(note)}


async def accept_invitation(client: BrowserClient, invitation_id: str) -> dict[str, Any]:
    """Accept an incoming connection invitation by navigating to /mynetwork."""
    await client.navigate(f"{LINKEDIN_BASE}/mynetwork/invitation-manager/")
    tree = await client.snapshot()
    accept_ref = _find_ref_in_snapshot(
        tree, name_match=r"^accept$", role="button"
    )
    if not accept_ref:
        return {"ok": False, "invitation_id": invitation_id, "error": "no_accept_button"}
    await client.click(accept_ref)
    return {"ok": True, "invitation_id": invitation_id}


async def decline_invitation(client: BrowserClient, invitation_id: str) -> dict[str, Any]:
    await client.navigate(f"{LINKEDIN_BASE}/mynetwork/invitation-manager/")
    tree = await client.snapshot()
    decline_ref = _find_ref_in_snapshot(
        tree, name_match=r"^ignore$|^decline$", role="button"
    )
    if not decline_ref:
        return {"ok": False, "invitation_id": invitation_id, "error": "no_decline_button"}
    await client.click(decline_ref)
    return {"ok": True, "invitation_id": invitation_id}


async def withdraw_invitation(client: BrowserClient, invitation_id: str) -> dict[str, Any]:
    await client.navigate(f"{LINKEDIN_BASE}/mynetwork/invitation-manager/sent/")
    tree = await client.snapshot()
    withdraw_ref = _find_ref_in_snapshot(
        tree, name_match=r"^withdraw$|^recall$", role="button"
    )
    if not withdraw_ref:
        return {"ok": False, "invitation_id": invitation_id, "error": "no_withdraw_button"}
    await client.click(withdraw_ref)
    return {"ok": True, "invitation_id": invitation_id}


# ---------------------------------------------------------------------------
# Snapshot parsing helpers
# ---------------------------------------------------------------------------


def _find_ref_in_snapshot(
    tree: str, *, name_match: str = "", role: str = ""
) -> str | None:
    """Find a ref (@eN) for an element matching name pattern and role.

    agent-browser snapshot format (simplified):
        - button "Connect" [ref=e15]
        - link "Send message" [ref=e23]

    The ref is the bracketed token after the role.
    """
    if not tree:
        return None
    name_re = re.compile(name_match, re.I) if name_match else None
    for line in tree.splitlines():
        # Strip leading bullet / dash
        s = line.lstrip("-* ").strip()
        # Match role prefix
        if role and not s.lower().startswith(role):
            continue
        # Extract quoted name (first quoted string after the role)
        m_name = re.search(r'"([^"]*)"', s)
        if not m_name:
            continue
        name = m_name.group(1)
        if name_re and not name_re.search(name):
            continue
        # Extract ref
        m_ref = re.search(r"\[ref=([^\]]+)\]", s)
        if m_ref:
            return f"@{m_ref.group(1)}"
    return None
