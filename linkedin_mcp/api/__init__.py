"""Voyager API client — read-only LinkedIn HTTP access.

Public surface:

* :class:`VoyagerClient` — async context manager, the single HTTP entry point
* :class:`AuthError`, :class:`RateLimitError`, :class:`VoyagerAPIError` — typed errors
* Endpoint helpers, one per Voyager route (profile, search, feed, messaging, jobs)
"""

from __future__ import annotations

from .client import (
    ACCEPT_HEADER,
    RESTLI_PROTOCOL_VERSION,
    USER_AGENT,
    VOYAGER_BASE_URL,
    AuthError,
    RateLimitError,
    VoyagerAPIError,
    VoyagerClient,
)
from .feed import get_feed
from .jobs import get_company_employees, get_job_details
from .messaging import (
    get_conversation,
    get_inbox,
    get_pending_invitations,
)
from .profile import get_my_profile, get_person_profile
from .search import search_companies, search_jobs, search_people

__all__ = [
    # core
    "VoyagerClient",
    "VoyagerAPIError",
    "AuthError",
    "RateLimitError",
    "VOYAGER_BASE_URL",
    "USER_AGENT",
    "ACCEPT_HEADER",
    "RESTLI_PROTOCOL_VERSION",
    # profile
    "get_my_profile",
    "get_person_profile",
    # search
    "search_people",
    "search_jobs",
    "search_companies",
    # feed
    "get_feed",
    # messaging
    "get_inbox",
    "get_conversation",
    "get_pending_invitations",
    # jobs
    "get_job_details",
    "get_company_employees",
]
