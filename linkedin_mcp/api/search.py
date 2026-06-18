"""Search endpoints — people, jobs, companies.

These are thin wrappers over Voyager's search cluster endpoints. The exact
response shape (clusters of "elements" with hit highlights) is left to the
caller to interpret — we just return the raw dict.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .client import VoyagerClient

log = logging.getLogger("linkedin_mcp.api.search")


async def search_people(
    client: VoyagerClient,
    keywords: str,
    *,
    count: int = 10,
    start: int = 0,
    filters: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Search for people.

    Endpoint: ``GET /voyager/api/search/dash/clusters``
    """
    if not keywords:
        raise ValueError("keywords is required")
    params: dict[str, Any] = {
        "keywords": keywords,
        "count": str(count),
        "start": str(start),
        "q": "people",
    }
    if filters:
        params.update(filters)
    log.debug("search_people keywords=%r count=%d", keywords, count)
    return await client.get("/search/dash/clusters", params=params)


async def search_jobs(
    client: VoyagerClient,
    keywords: str,
    *,
    location: Optional[str] = None,
    count: int = 10,
    start: int = 0,
) -> dict[str, Any]:
    """Search for jobs.

    Endpoint: ``GET /voyager/api/jobs/search``
    """
    if not keywords:
        raise ValueError("keywords is required")
    params: dict[str, Any] = {
        "keywords": keywords,
        "count": str(count),
        "start": str(start),
    }
    if location:
        params["location"] = location
    log.debug("search_jobs keywords=%r location=%r", keywords, location)
    return await client.get("/jobs/search", params=params)


async def search_companies(
    client: VoyagerClient,
    keywords: str,
    *,
    count: int = 10,
    start: int = 0,
) -> dict[str, Any]:
    """Search for companies.

    Endpoint: ``GET /voyager/api/search/dash/clusters``
    """
    if not keywords:
        raise ValueError("keywords is required")
    params: dict[str, Any] = {
        "keywords": keywords,
        "count": str(count),
        "start": str(start),
        "q": "companies",
    }
    log.debug("search_companies keywords=%r", keywords)
    return await client.get("/search/dash/clusters", params=params)
