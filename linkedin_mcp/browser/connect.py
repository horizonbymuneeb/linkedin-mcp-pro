"""Connection-related actions: send, accept, decline, withdraw.

All actions navigate to the target's profile, find the Connect button via
the agent-browser snapshot, and click it. Personalized notes go through a
second "Add a note" dialog.

Note template rotation:
    Sending the same note to multiple people creates a fingerprint that
    LinkedIn can detect as automated behavior. To avoid this, we provide
    a set of templates and rotate through them with light variation.

    If a custom note is passed to ``send_connection_request``, that note is
    used verbatim (LLM-personalized paths are the user's responsibility).
    If no note is passed, the safety layer picks from the configured
    templates via ``pick_note()`` (see below).
"""

from __future__ import annotations

import logging
import random
import re
from typing import Any

from .client import BrowserClient, LINKEDIN_BASE

log = logging.getLogger("linkedin_mcp.browser.connect")

MAX_NOTE_LENGTH = 300
_VALID_PUBLIC_ID = re.compile(r"^[a-zA-Z0-9\-_.]{3,100}$")

# Default templates. Callers can override via config.NOTE_TEMPLATES.
_DEFAULT_TEMPLATES: tuple[str, ...] = (
    "Hi {first_name} — saw your work on {topic}. I'm building similar things in {my_field}, would love to compare notes.",
    "Hey {first_name}, your post about {topic} resonated. Fellow {my_field} person here, would enjoy connecting.",
    "Hi {first_name} — noticed we're both working in {my_field}. I recently {my_activity}, would love to chat.",
    "{first_name}, your background at {company} is interesting. I'm in {my_field}, just {my_activity}. Let's connect.",
    "Hi {first_name} — came across your profile while looking for {my_field} folks. Would love to be in touch.",
)


def _validate_public_id(public_id: str) -> None:
    if not public_id or not _VALID_PUBLIC_ID.match(public_id):
        raise ValueError(
            f"Invalid public_id {public_id!r}: must be 3-100 chars, "
            f"alphanumeric + - _ ."
        )


def _validate_note(note: str) -> None:
    if len(note) > MAX_NOTE_LENGTH:
        raise ValueError(f"Note too long: {len(note)} > {MAX_NOTE_LENGTH}")


def pick_note(
    *,
    first_name: str = "",
    topic: str = "your work",
    my_field: str = "tech",
    my_activity: str = "shipped a new project",
    company: str = "your company",
    templates: tuple[str, ...] | list[str] | None = None,
) -> str:
    """Pick a random note template and fill in variables.

    Use this when you don't have a personalized note from the LLM but still
    want the safety benefit of varied wording across connection requests.

    All variables are optional. Default fillers keep the note grammatical
    even with no context.
    """
    pool = tuple(templates) if templates else _DEFAULT_TEMPLATES
    template = random.choice(pool)
    return template.format(
        first_name=first_name or "there",
        topic=topic,
        my_field=my_field,
        my_activity=my_activity,
        company=company,
    ).strip()


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
