"""Feed endpoint — your home timeline."""

from __future__ import annotations

import logging
from typing import Any, Optional

from .client import VoyagerClient

log = logging.getLogger("linkedin_mcp.api.feed")


async def get_feed(
    client: VoyagerClient,
    *,
    count: int = 20,
    start: int = 0,
) -> dict[str, Any]:
    """Return the home feed (timeline) updates.

    Endpoint: ``GET /voyager/api/feed/updates``
    """
    params: dict[str, Any] = {"count": str(count), "start": str(start)}
    log.debug("get_feed count=%d start=%d", count, start)
    return await client.get("/feed/updates", params=params)
