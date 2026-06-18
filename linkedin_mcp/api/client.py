"""Voyager API HTTP client for linkedin-mcp-pro.

Thin wrapper around ``httpx.AsyncClient`` that adds:

* realistic browser headers (User-Agent, Accept, x-restli-protocol-version)
* ``csrf-token`` and ``Cookie`` headers built from ``li_at`` / ``jsessionid``
* error translation: 401/403 → :class:`AuthError`, 429 → :class:`RateLimitError`,
  5xx → automatic retry via tenacity
* optional audit logging when a :class:`~linkedin_mcp.db.DB` is supplied

Each endpoint module (``profile``, ``search``, ``feed`` …) wraps ``client.get``
with a small function. Callers receive the raw JSON dict — we deliberately do
not try to fully parse LinkedIn's nested ``*Elements`` / ``included`` shape.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

import httpx
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

if TYPE_CHECKING:
    from ..db import DB

log = logging.getLogger("linkedin_mcp.api")

VOYAGER_BASE_URL = "https://www.linkedin.com/voyager/api"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
ACCEPT_HEADER = "application/vnd.linkedin.normalized+json+2.1"
RESTLI_PROTOCOL_VERSION = "2.0.0"
DEFAULT_TIMEOUT = 30.0
MAX_5XX_RETRIES = 3


class VoyagerAPIError(Exception):
    """Base error for the Voyager API client."""


class AuthError(VoyagerAPIError):
    """Raised on 401/403 — cookie is invalid or expired. Re-login required."""


class RateLimitError(VoyagerAPIError):
    """Raised on 429. Safety layer should back off and retry later."""

    def __init__(self, message: str, *, retry_after_seconds: Optional[int] = None) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(message)


class VoyagerClient:
    """Async HTTP client for LinkedIn's Voyager API.

    Usage::

        async with VoyagerClient(li_at="...", jsessionid="...") as client:
            me = await client.get("/me")
            profile = await client.get("/people/(publicIdentifier:alice)")

    Pass ``db=`` to have every call audited (``action="voyager"``).
    """

    def __init__(
        self,
        li_at: str,
        jsessionid: Optional[str] = None,
        *,
        db: Optional["DB"] = None,
        base_url: str = VOYAGER_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if not li_at:
            raise ValueError("li_at is required (get it from li_at cookie)")
        self.li_at = li_at
        self.jsessionid = jsessionid
        self.db = db
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._http: Optional[httpx.AsyncClient] = None

    # ---- lifecycle ----------------------------------------------------

    async def __aenter__(self) -> "VoyagerClient":
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._build_headers(),
            timeout=self.timeout,
            follow_redirects=False,
        )
        log.debug("VoyagerClient opened base_url=%s", self.base_url)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
            log.debug("VoyagerClient closed")

    # ---- public API ---------------------------------------------------

    async def get(self, path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """GET ``path`` (relative to Voyager base) and return the parsed JSON dict.

        Translates HTTP errors into :class:`VoyagerAPIError` subclasses and
        retries 5xx responses with exponential backoff.
        """
        if self._http is None:
            raise RuntimeError("VoyagerClient used outside `async with` block")

        url = path if path.startswith("/") else f"/{path}"
        attempt = 0
        last_exc: Optional[Exception] = None

        try:
            async for retry in AsyncRetrying(
                stop=stop_after_attempt(MAX_5XX_RETRIES + 1),
                wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
                retry=retry_if_exception_type(httpx.HTTPStatusError),
                reraise=True,
            ):
                with retry:
                    attempt += 1
                    try:
                        response = await self._http.get(url, params=params)
                    except httpx.TransportError as exc:
                        # Network-level failure (DNS, connection reset…) — retry too.
                        log.warning("transport error on %s (attempt %d): %s", url, attempt, exc)
                        if attempt > MAX_5XX_RETRIES:
                            raise
                        # Coerce into HTTPStatusError so tenacity retries uniformly.
                        raise httpx.HTTPStatusError(
                            f"transport error: {exc}",
                            request=httpx.Request("GET", url),
                            response=httpx.Response(599, request=httpx.Request("GET", url)),
                        ) from exc

                    if response.status_code >= 500:
                        # Force a retry — wrap the response in HTTPStatusError.
                        log.warning(
                            "server error %s on %s (attempt %d)",
                            response.status_code, url, attempt,
                        )
                        # raise_for_status will re-raise; the wrapped response carries
                        # the status code so the caller can log it on final failure.
                        raise httpx.HTTPStatusError(
                            f"server error {response.status_code}",
                            request=response.request,
                            response=response,
                        )

                    self._raise_for_status(response)
                    self._audit(path, response, params)
                    return response.json()
        except RetryError as exc:
            # tenacity gave up — surface the last underlying exception.
            raise exc.last_attempt.exception() from exc  # type: ignore[misc]

        # Unreachable — the loop either returns or raises.
        raise RuntimeError("unreachable: get() returned without result")  # pragma: no cover

    # ---- helpers ------------------------------------------------------

    def _build_headers(self) -> dict[str, str]:
        # LinkedIn accepts the li_at value as csrf-token when JSESSIONID is
        # absent — it's a well-known workaround used by every open-source
        # Voyager client.
        csrf = self.jsessionid or self.li_at
        cookie_parts = [f"li_at={self.li_at}"]
        if self.jsessionid:
            cookie_parts.append(f"JSESSIONID={self.jsessionid}")
        return {
            "User-Agent": USER_AGENT,
            "Accept": ACCEPT_HEADER,
            "x-restli-protocol-version": RESTLI_PROTOCOL_VERSION,
            "csrf-token": csrf,
            "Cookie": "; ".join(cookie_parts),
            "Accept-Language": "en-US,en;q=0.9",
        }

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        """Convert 401/403/429 into typed errors. Other non-2xx → HTTPStatusError."""
        code = response.status_code
        if code in (401, 403):
            raise AuthError(
                f"authentication failed ({code}); li_at/JSESSIONID expired — re-login required"
            )
        if code == 429:
            retry_after = response.headers.get("Retry-After")
            seconds = int(retry_after) if (retry_after and retry_after.isdigit()) else None
            raise RateLimitError(
                f"rate limited (429); back off and retry later",
                retry_after_seconds=seconds,
            )
        if 400 <= code < 600:
            # 4xx (other) and any 5xx that fell through (shouldn't normally)
            raise httpx.HTTPStatusError(
                f"unexpected status {code}",
                request=response.request,
                response=response,
            )

    def _audit(
        self,
        path: str,
        response: httpx.Response,
        params: Optional[dict[str, Any]],
    ) -> None:
        if self.db is None:
            return
        try:
            self.db.audit(
                action="voyager",
                status="success" if response.status_code < 400 else "failed",
                target=path,
                detail={
                    "method": "GET",
                    "status": response.status_code,
                    "params": params or {},
                },
            )
        except Exception as exc:  # pragma: no cover — never let audit break the call
            log.debug("audit logging failed: %s", exc)
