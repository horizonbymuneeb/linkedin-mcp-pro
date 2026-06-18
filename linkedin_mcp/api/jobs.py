"""Job details + company-employees lookups."""

from __future__ import annotations

import logging
from typing import Any, Optional

from .client import VoyagerClient

log = logging.getLogger("linkedin_mcp.api.jobs")


async def get_job_details(
    client: VoyagerClient,
    job_id: str,
) -> dict[str, Any]:
    """Return full details for a single job posting.

    Endpoint: ``GET /voyager/api/jobs/jobPostings/{id}``
    """
    if not job_id:
        raise ValueError("job_id is required")
    log.debug("get_job_details id=%s", job_id)
    return await client.get(f"/jobs/jobPostings/{job_id}")


async def get_company_employees(
    client: VoyagerClient,
    company_id: str,
    *,
    count: int = 20,
    start: int = 0,
) -> dict[str, Any]:
    """Return employees of a company (search-by-company cluster).

    Endpoint: ``GET /voyager/api/search/dash/clusters``
    """
    if not company_id:
        raise ValueError("company_id is required")
    # Voyager exposes people-search with a currentCompany filter via the
    # search/cluster endpoint. We send a `currentCompany` param (the numeric
    # company id) and a `q=people` selector.
    params: dict[str, Any] = {
        "currentCompany": company_id,
        "q": "people",
        "count": str(count),
        "start": str(start),
    }
    log.debug("get_company_employees company_id=%s", company_id)
    return await client.get("/search/dash/clusters", params=params)
