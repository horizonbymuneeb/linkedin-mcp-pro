"""Messaging endpoints — inbox and per-conversation read.

READ-ONLY: we never send messages via the Voyager API. Sending is a write
action and must go through the browser automation layer.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .client import VoyagerClient

log = logging.getLogger("linkedin_mcp.api.messaging")


async def get_inbox(
    client: VoyagerClient,
    *,
    count: int = 20,
    start: int = 0,
) -> dict[str, Any]:
    """Return the user's messaging inbox (list of conversations).

    Endpoint: ``GET /voyager/api/messaging/conversations``
    """
    params: dict[str, Any] = {"count": str(count), "start": str(start)}
    log.debug("get_inbox count=%d start=%d", count, start)
    return await client.get("/messaging/conversations", params=params)


async def get_conversation(
    client: VoyagerClient,
    conversation_id: str,
) -> dict[str, Any]:
    """Return the messages in a single conversation.

    Endpoint: ``GET /voyager/api/messaging/conversations/{id}/events``
    """
    if not conversation_id:
        raise ValueError("conversation_id is required")
    log.debug("get_conversation id=%s", conversation_id)
    return await client.get(
        f"/messaging/conversations/{conversation_id}/events"
    )


async def get_pending_invitations(
    client: VoyagerClient,
    *,
    count: int = 50,
    start: int = 0,
) -> dict[str, Any]:
    """List sent connection invitations awaiting response.

    Endpoint: ``GET /voyager/api/voyagerRelationshipsDashInvitations``
    """
    params: dict[str, Any] = {
        "count": str(count),
        "start": str(start),
        "q": "invitations",
        "invitationType": "PENDING",
    }
    log.debug("get_pending_invitations count=%d start=%d", count, start)
    return await client.get(
        "/voyagerRelationshipsDashInvitations", params=params
    )
