"""Profile endpoints — your own profile and people-by-vanity."""

from __future__ import annotations

import logging
from typing import Any

from .client import VoyagerClient

log = logging.getLogger("linkedin_mcp.api.profile")


async def get_my_profile(client: VoyagerClient) -> dict[str, Any]:
    """Return the authenticated user's own profile payload.

    Endpoint: ``GET /voyager/api/me``
    """
    log.debug("get_my_profile")
    return await client.get("/me")


async def get_person_profile(
    client: VoyagerClient, public_id: str
) -> dict[str, Any]:
    """Return a person's profile by public/vanity identifier.

    Endpoint: ``GET /voyager/api/people/(publicIdentifier:{public_id})``
    """
    if not public_id:
        raise ValueError("public_id is required")
    log.debug("get_person_profile public_id=%s", public_id)
    return await client.get(f"/people/(publicIdentifier:{public_id})")
