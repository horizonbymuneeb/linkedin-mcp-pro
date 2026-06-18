"""BrowserClient — async wrapper around the `agent-browser` CLI.

Why `agent-browser` (over Patchright / Playwright):

  * **Real Chrome** (Chrome for Testing) — better stealth than Chromium.
  * **AI-native design** — `snapshot` returns accessibility tree with refs
    (``@e2``, ``@e3``) that the LLM/MCP layer can act on.
  * **Vercel Labs backed**, Apache-2.0, 36k+ stars, very active.
  * **Smaller deps** — Rust binary, no Chromium download.
  * **Trade-off**: subprocess overhead (~50-100ms/call). For our use case
    (max 1-2 actions per minute with safety jitter), this is invisible.

This module translates the async-Python interface we need into a sequence of
``agent-browser`` CLI calls. Captcha/429 detection runs after every
navigation and delegates to ``SafetyGuard`` for the actual side effects.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Optional

from ..config import Config
from ..db import DB
from ..safety import (
    CaptchaDetectedError,
    RateLimitedError,
    SafetyGuard,
    detect_captcha_in_text,
)

log = logging.getLogger("linkedin_mcp.browser")

# Patterns indicating LinkedIn served a 429 / rate-limit page
_RATE_LIMIT_PATTERNS = [
    re.compile(r"you'?re temporarily limited", re.I),
    re.compile(r"rate limit", re.I),
    re.compile(r"slow down", re.I),
    re.compile(r"too many requests", re.I),
    re.compile(r"we'?re limiting activity", re.I),
]
_RATE_LIMIT_HOSTS = ("restricted.linkedin.com", "checkpoint.linkedin.com")

LINKEDIN_BASE = "https://www.linkedin.com"

# Per-call timeout. Long actions (post create) can take longer; bump per-call.
_DEFAULT_TIMEOUT = 60.0
_LONG_TIMEOUT = 180.0


class BrowserError(Exception):
    """Raised on unrecoverable browser failure (auth wall, network, etc.)."""


def _find_agent_browser() -> str:
    """Locate the agent-browser binary on PATH."""
    p = shutil.which("agent-browser")
    if not p:
        raise BrowserError(
            "agent-browser not found. Install: npm i -g agent-browser && agent-browser install --with-deps"
        )
    return p


def _set_li_at_cookie_sync(profile_dir: Path, li_at: str) -> None:
    """Write li_at into agent-browser's profile dir as a JSON cookie file.

    agent-browser stores cookies in a Playwright-compatible storage_state.json
    inside its profile dir. We can pre-populate li_at there so the first
    launch is already authenticated.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    state_file = profile_dir / "storage_state.json"
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
        except json.JSONDecodeError:
            state = {"cookies": [], "origins": []}
    else:
        state = {"cookies": [], "origins": []}

    # Replace any existing li_at cookie
    state["cookies"] = [c for c in state.get("cookies", []) if c.get("name") != "li_at"]
    state["cookies"].append({
        "name": "li_at",
        "value": li_at,
        "domain": ".linkedin.com",
        "path": "/",
        "expires": -1,
        "httpOnly": True,
        "secure": True,
        "sameSite": "None",
    })
    state_file.write_text(json.dumps(state, indent=2))


class BrowserClient:
    """Async wrapper around the `agent-browser` CLI.

    Usage::

        async with BrowserClient(cfg, db, guard) as br:
            await br.navigate("https://www.linkedin.com/in/satyam-code")
            tree = await br.snapshot()
            # tree contains refs like @e2
            await br.click("@e5")
            text = await br.get_text("@e7")
    """

    def __init__(self, cfg: Config, db: DB, guard: SafetyGuard):
        self.cfg = cfg
        self.db = db
        self.guard = guard
        self.binary = _find_agent_browser()
        self.profile_dir = Path(cfg.storage.browser_profile_dir)
        self._current_url: str = ""
        self._current_title: str = ""
        self._proc: Optional[asyncio.subprocess.Process] = None

    # ---- lifecycle -----------------------------------------------------

    async def __aenter__(self) -> "BrowserClient":
        # Pre-set li_at cookie so the session is authenticated from the start
        if self.cfg.li_at:
            _set_li_at_cookie_sync(self.profile_dir, self.cfg.li_at)
        log.info("BrowserClient ready. Profile: %s", self.profile_dir)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        # Best-effort close (don't raise)
        try:
            await self.close()
        except Exception as e:  # noqa: BLE001
            log.debug("close() on exit failed: %s", e)

    # ---- core agent-browser commands ----------------------------------

    async def _run(
        self,
        *args: str,
        timeout: float = _DEFAULT_TIMEOUT,
        check: bool = True,
    ) -> tuple[int, str, str]:
        """Run an agent-browser command, return (rc, stdout, stderr)."""
        cmd = [self.binary, *args]
        log.debug("$ %s", " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise BrowserError(f"agent-browser timeout: {' '.join(cmd[:5])}...")
        rc = proc.returncode or 0
        out = stdout.decode(errors="replace") if stdout else ""
        err = stderr.decode(errors="replace") if stderr else ""
        if check and rc != 0:
            log.warning("agent-browser rc=%d: %s | err=%s", rc, cmd[:4], err[:200])
        return rc, out, err

    # ---- high-level API ------------------------------------------------

    async def navigate(self, url: str, *, wait_for: Optional[str] = None) -> None:
        """Navigate to URL and verify we landed (no auth wall, no captcha)."""
        rc, out, err = await self._run("open", url, timeout=_LONG_TIMEOUT)
        if rc != 0 and "no such file" in (err + out).lower():
            # agent-browser may not have an open session — try with explicit launch
            rc, out, err = await self._run("open", url, timeout=_LONG_TIMEOUT)
        # Parse URL from output (typically "✓ <title>\n  <url>")
        self._current_url = url
        for line in out.splitlines():
            stripped = line.strip()
            if stripped.startswith("http://") or stripped.startswith("https://"):
                self._current_url = stripped
                break
        log.info("navigated to %s", self._current_url)

        # Check for auth wall
        if any(p in self._current_url for p in ("/login", "/authwall", "/checkpoint", "/uas/")):
            raise BrowserError(
                f"Auth wall detected at {self._current_url}. "
                f"Refresh your li_at cookie (browser DevTools → Application → Cookies → li_at)."
            )

        # Check for captcha / 429 in page text
        await self._check_for_challenges()

    async def snapshot(self) -> str:
        """Return the accessibility tree (refs in `@e2` format)."""
        rc, out, err = await self._run("snapshot", timeout=_DEFAULT_TIMEOUT)
        return out

    async def click(self, selector: str) -> None:
        """Click an element by ref (@e2) or CSS selector."""
        await self._run("click", selector, timeout=_DEFAULT_TIMEOUT)
        await self._check_for_challenges()

    async def fill(self, selector: str, text: str) -> None:
        """Clear and fill a field by ref or CSS selector."""
        await self._run("fill", selector, text, timeout=_DEFAULT_TIMEOUT)

    async def type_text(self, text: str) -> None:
        """Type text at current focus (no selector)."""
        await self._run("keyboard", "type", text, timeout=_DEFAULT_TIMEOUT)

    async def press(self, key: str) -> None:
        """Press a key (Enter, Tab, etc.)."""
        await self._run("press", key, timeout=_DEFAULT_TIMEOUT)

    async def get_text(self, selector: str) -> str:
        """Get text content of an element."""
        rc, out, err = await self._run("get", "text", selector, timeout=_DEFAULT_TIMEOUT)
        return out.strip()

    async def get_url(self) -> str:
        """Get the current page URL."""
        rc, out, err = await self._run("get", "url", timeout=_DEFAULT_TIMEOUT)
        self._current_url = out.strip()
        return self._current_url

    async def get_title(self) -> str:
        """Get the current page title."""
        rc, out, err = await self._run("get", "title", timeout=_DEFAULT_TIMEOUT)
        self._current_title = out.strip()
        return self._current_title

    async def wait_for(self, selector: str, *, timeout_ms: int = 30_000) -> None:
        """Wait for an element to appear (ref or CSS)."""
        await self._run("wait-for-selector", selector, f"--timeout={timeout_ms}",
                        timeout=_DEFAULT_TIMEOUT + timeout_ms / 1000)

    async def screenshot(self, path: str) -> None:
        """Take a screenshot to a file path."""
        await self._run("screenshot", path, timeout=_LONG_TIMEOUT)

    async def eval(self, js: str) -> str:
        """Run JavaScript in the page and return the result."""
        rc, out, err = await self._run("eval", js, timeout=_DEFAULT_TIMEOUT)
        return out.strip()

    async def close(self) -> None:
        """Close the browser session."""
        try:
            await self._run("close", check=False, timeout=10.0)
        except Exception as e:  # noqa: BLE001
            log.debug("agent-browser close failed: %s", e)

    # ---- internal helpers ---------------------------------------------

    async def _check_for_challenges(self) -> None:
        """After navigation, check for captcha / 429 / restricted pages.

        Side effects: calls ``guard.record_captcha()`` or ``guard.record_429()``,
        which pause writes for 24h or trigger exponential backoff.
        """
        url = self._current_url
        # 1. URL-based detection
        if any(host in url for host in _RATE_LIMIT_HOSTS):
            log.warning("Rate-limit host detected: %s", url)
            from ..safety import ActionPlan
            self.guard.record_429()
            raise RateLimitedError(
                f"Rate limit page: {url}", retry_after_seconds=300
            )

        # 2. Body text-based detection
        try:
            body = await self.eval("document.body.innerText || ''")
        except BrowserError:
            return  # page didn't load; skip

        if detect_captcha_in_text(body):
            log.error("CAPTCHA detected on %s", url)
            from ..safety import ActionPlan
            self.guard.record_captcha(ActionPlan(action="page_check", target=url, payload={}))
            raise CaptchaDetectedError(f"Captcha detected on {url}")

        for pat in _RATE_LIMIT_PATTERNS:
            if pat.search(body):
                log.warning("Rate-limit text detected: %s", pat.pattern)
                self.guard.record_429()
                raise RateLimitedError(
                    f"Rate limit text: {pat.pattern}", retry_after_seconds=300
                )
