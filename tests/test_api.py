"""Tests for the Voyager API client.

Uses ``httpx.MockTransport`` (built into httpx, no new dep) to avoid real
network calls. Covers:

* header construction (User-Agent, Accept, csrf-token, Cookie)
* happy-path GET on every endpoint helper
* 401/403 → AuthError
* 429 → RateLimitError (with Retry-After honoured)
* 5xx → retries then raises
* audit logging when DB is supplied
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

import httpx
import pytest

from linkedin_mcp.api import (
    AuthError,
    RateLimitError,
    VoyagerClient,
    VoyagerAPIError,
    get_company_employees,
    get_conversation,
    get_feed,
    get_inbox,
    get_job_details,
    get_my_profile,
    get_person_profile,
    search_companies,
    search_jobs,
    search_people,
)
from linkedin_mcp.api.client import (
    ACCEPT_HEADER,
    RESTLI_PROTOCOL_VERSION,
    USER_AGENT,
    VOYAGER_BASE_URL,
)


# ---- fixtures ----------------------------------------------------------------


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    li_at: str = "test-li_at-123",
    jsessionid: Optional[str] = "test-jsessionid-abc",
    db: Optional[Any] = None,
) -> VoyagerClient:
    """Build a VoyagerClient whose internal httpx.AsyncClient uses a MockTransport.

    We reach in and replace ``_http`` after ``__aenter__`` because the
    AsyncClient is constructed inside the context manager.
    """
    client = VoyagerClient(li_at=li_at, jsessionid=jsessionid, db=db)
    # We must not call __aenter__ (which creates a real AsyncClient); instead
    # construct the transport manually and inject it.
    mock_transport = httpx.MockTransport(handler)
    client._http = httpx.AsyncClient(
        base_url=client.base_url,
        headers=client._build_headers(),
        timeout=client.timeout,
        transport=mock_transport,
    )
    return client


async def aclose_client(client: VoyagerClient) -> None:
    if client._http is not None:
        await client._http.aclose()
        client._http = None


def json_response(payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        content=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
    )


# ---- header construction -----------------------------------------------------


def test_build_headers_with_jsessionid() -> None:
    """JSESSIONID present → csrf-token == jsessionid, cookie has both."""
    c = VoyagerClient(li_at="LIAT", jsessionid="JSID")
    h = c._build_headers()
    assert h["User-Agent"] == USER_AGENT
    assert h["Accept"] == ACCEPT_HEADER
    assert h["x-restli-protocol-version"] == RESTLI_PROTOCOL_VERSION
    assert h["csrf-token"] == "JSID"
    assert "li_at=LIAT" in h["Cookie"]
    assert "JSESSIONID=JSID" in h["Cookie"]


def test_build_headers_without_jsessionid() -> None:
    """No JSESSIONID → csrf-token falls back to li_at, cookie has only li_at."""
    c = VoyagerClient(li_at="LIAT", jsessionid=None)
    h = c._build_headers()
    assert h["csrf-token"] == "LIAT"
    assert h["Cookie"] == "li_at=LIAT"


def test_constructor_requires_li_at() -> None:
    with pytest.raises(ValueError, match="li_at is required"):
        VoyagerClient(li_at="")  # type: ignore[arg-type]


# ---- happy path --------------------------------------------------------------


async def test_get_returns_parsed_json() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/voyager/api/me"
        return json_response({"miniProfile": {"vanityName": "alice"}})

    c = make_client(handler)
    try:
        data = await c.get("/me")
    finally:
        await aclose_client(c)
    assert data == {"miniProfile": {"vanityName": "alice"}}


async def test_get_passes_params() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return json_response({"ok": True})

    c = make_client(handler)
    try:
        await c.get("/feed/updates", params={"count": "5", "start": "0"})
    finally:
        await aclose_client(c)
    assert len(seen) == 1
    assert seen[0].url.params["count"] == "5"
    assert seen[0].url.params["start"] == "0"


async def test_get_uses_voyager_base_url() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "www.linkedin.com"
        assert req.url.path.startswith("/voyager/api/")
        return json_response({"ok": True})

    c = make_client(handler)
    try:
        await c.get("/me")
    finally:
        await aclose_client(c)


# ---- endpoint helpers --------------------------------------------------------


async def test_get_my_profile() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/voyager/api/me"
        return json_response({"vanityName": "me"})

    c = make_client(handler)
    try:
        out = await get_my_profile(c)
    finally:
        await aclose_client(c)
    assert out == {"vanityName": "me"}


async def test_get_person_profile_builds_path() -> None:
    seen_path: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_path.append(req.url.path)
        return json_response({"vanityName": "alice"})

    c = make_client(handler)
    try:
        out = await get_person_profile(c, "alice")
    finally:
        await aclose_client(c)
    assert seen_path == ["/voyager/api/people/(publicIdentifier:alice)"]
    assert out == {"vanityName": "alice"}


async def test_get_person_profile_rejects_empty_id() -> None:
    c = make_client(lambda r: json_response({}))
    try:
        with pytest.raises(ValueError, match="public_id is required"):
            await get_person_profile(c, "")
    finally:
        await aclose_client(c)


async def test_search_people_passes_keywords() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.params["keywords"] == "python devs"
        assert req.url.params["q"] == "people"
        assert req.url.params["count"] == "5"
        return json_response({"results": []})

    c = make_client(handler)
    try:
        out = await search_people(c, "python devs", count=5)
    finally:
        await aclose_client(c)
    assert out == {"results": []}


async def test_search_jobs_passes_location() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.params["keywords"] == "swe"
        assert req.url.params["location"] == "Berlin"
        assert req.url.path == "/voyager/api/jobs/search"
        return json_response({"elements": []})

    c = make_client(handler)
    try:
        out = await search_jobs(c, "swe", location="Berlin")
    finally:
        await aclose_client(c)
    assert out == {"elements": []}


async def test_search_companies() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.params["q"] == "companies"
        assert req.url.params["keywords"] == "anthropic"
        return json_response({"results": []})

    c = make_client(handler)
    try:
        out = await search_companies(c, "anthropic")
    finally:
        await aclose_client(c)
    assert out == {"results": []}


async def test_get_feed() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/voyager/api/feed/updates"
        assert req.url.params["count"] == "10"
        return json_response({"elements": [1, 2, 3]})

    c = make_client(handler)
    try:
        out = await get_feed(c, count=10)
    finally:
        await aclose_client(c)
    assert out == {"elements": [1, 2, 3]}


async def test_get_inbox() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/voyager/api/messaging/conversations"
        return json_response({"elements": []})

    c = make_client(handler)
    try:
        out = await get_inbox(c)
    finally:
        await aclose_client(c)
    assert out == {"elements": []}


async def test_get_conversation() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/voyager/api/messaging/conversations/C-42/events"
        return json_response({"events": []})

    c = make_client(handler)
    try:
        out = await get_conversation(c, "C-42")
    finally:
        await aclose_client(c)
    assert out == {"events": []}


async def test_get_job_details() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/voyager/api/jobs/jobPostings/12345"
        return json_response({"title": "Staff SWE"})

    c = make_client(handler)
    try:
        out = await get_job_details(c, "12345")
    finally:
        await aclose_client(c)
    assert out == {"title": "Staff SWE"}


async def test_get_company_employees() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.params["currentCompany"] == "9876"
        assert req.url.params["q"] == "people"
        return json_response({"elements": []})

    c = make_client(handler)
    try:
        out = await get_company_employees(c, "9876")
    finally:
        await aclose_client(c)
    assert out == {"elements": []}


# ---- error handling: 401/403 --------------------------------------------------


@pytest.mark.parametrize("code", [401, 403])
async def test_auth_error(code: int) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return json_response({"error": "auth"}, status=code)

    c = make_client(handler)
    try:
        with pytest.raises(AuthError) as excinfo:
            await c.get("/me")
        assert "re-login" in str(excinfo.value).lower()
    finally:
        await aclose_client(c)


# ---- error handling: 429 -----------------------------------------------------


async def test_rate_limit_with_retry_after_header() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            content=b"rate limited",
            headers={"Retry-After": "120"},
        )

    c = make_client(handler)
    try:
        with pytest.raises(RateLimitError) as excinfo:
            await c.get("/me")
        assert excinfo.value.retry_after_seconds == 120
    finally:
        await aclose_client(c)


async def test_rate_limit_without_retry_after_header() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return json_response({"error": "throttled"}, status=429)

    c = make_client(handler)
    try:
        with pytest.raises(RateLimitError) as excinfo:
            await c.get("/me")
        assert excinfo.value.retry_after_seconds is None
    finally:
        await aclose_client(c)


# ---- error handling: 5xx retries ---------------------------------------------


async def test_5xx_is_retried_then_raises() -> None:
    """Tenacity should retry MAX_5XX_RETRIES+1 times (4 total) on 500, then give up."""
    calls: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        return json_response({"error": "boom"}, status=500)

    c = make_client(handler)
    try:
        with pytest.raises(httpx.HTTPStatusError) as excinfo:
            await c.get("/me")
        assert excinfo.value.response.status_code == 500
        # tenacity configured: stop_after_attempt(MAX_5XX_RETRIES + 1) = 4
        assert len(calls) == 4
    finally:
        await aclose_client(c)


async def test_5xx_recovers_on_retry() -> None:
    """If a retry returns 2xx, the client returns the parsed JSON normally."""
    calls: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 3:
            return json_response({"error": "boom"}, status=503)
        return json_response({"ok": True, "attempt": len(calls)})

    c = make_client(handler)
    try:
        out = await c.get("/me")
    finally:
        await aclose_client(c)
    assert out == {"ok": True, "attempt": 3}
    assert len(calls) == 3


# ---- context manager ---------------------------------------------------------


async def test_async_context_manager() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return json_response({"x": 1})

    c = VoyagerClient(li_at="abc")
    assert c._http is None
    async with c as opened:
        assert opened is c
        assert opened._http is not None
        # We never make a real call here — just verify the lifecycle.
    assert c._http is None


async def test_using_outside_context_manager_raises() -> None:
    c = VoyagerClient(li_at="abc")
    with pytest.raises(RuntimeError, match="outside `async with`"):
        await c.get("/me")


# ---- audit logging -----------------------------------------------------------


class _FakeDB:
    """Minimal stand-in for linkedin_mcp.db.DB — captures audit() calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def audit(self, action: str, status: str, target=None, dry_run: bool = False, detail=None):
        self.calls.append(
            {
                "action": action,
                "status": status,
                "target": target,
                "dry_run": dry_run,
                "detail": detail,
            }
        )
        return len(self.calls)


async def test_audit_logged_on_success() -> None:
    db = _FakeDB()

    def handler(req: httpx.Request) -> httpx.Response:
        return json_response({"ok": True})

    c = make_client(handler, db=db)
    try:
        await c.get("/me", params={"x": "1"})
    finally:
        await aclose_client(c)
    assert len(db.calls) == 1
    call = db.calls[0]
    assert call["action"] == "voyager"
    assert call["status"] == "success"
    assert call["target"] == "/me"
    assert call["detail"]["status"] == 200
    assert call["detail"]["params"] == {"x": "1"}


async def test_audit_not_logged_on_error() -> None:
    """Auth/rate-limit errors fire before _audit() — DB stays untouched."""
    db = _FakeDB()

    def handler(req: httpx.Request) -> httpx.Response:
        return json_response({}, status=401)

    c = make_client(handler, db=db)
    try:
        with pytest.raises(AuthError):
            await c.get("/me")
    finally:
        await aclose_client(c)
    assert db.calls == []


# ---- export surface ----------------------------------------------------------


def test_package_reexports() -> None:
    """Sanity check that the public API is what __init__ advertises."""
    import linkedin_mcp.api as api

    for name in (
        "VoyagerClient",
        "AuthError",
        "RateLimitError",
        "VoyagerAPIError",
        "get_my_profile",
        "get_person_profile",
        "search_people",
        "search_jobs",
        "search_companies",
        "get_feed",
        "get_inbox",
        "get_conversation",
        "get_job_details",
        "get_company_employees",
    ):
        assert hasattr(api, name), f"missing export: {name}"


def test_constants_match_design_requirements() -> None:
    """User-Agent, Accept, x-restli-protocol-version match the design doc."""
    assert "Chrome/120" in USER_AGENT
    assert ACCEPT_HEADER == "application/vnd.linkedin.normalized+json+2.1"
    assert RESTLI_PROTOCOL_VERSION == "2.0.0"
    assert VOYAGER_BASE_URL == "https://www.linkedin.com/voyager/api"
