"""Auth / session bootstrap for the browser module.

In v0.3.0+, the primary auth method is a persistent browser session that
the user creates once via ``linkedin-mcp login``:

    linkedin-mcp login   # opens browser, user logs in, profile saved
    linkedin-mcp serve   # all future calls use that profile

The ``interactive_login()`` function below implements the login flow:
opens the browser to LinkedIn's login page, waits for the user to log in
manually, and confirms the session is captured by polling for the
``/feed/`` URL pattern.

The legacy ``ensure_session()`` + ``LI_AT`` env-var flow is still
supported as a fallback for headless environments.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from ..config import Config
from .client import DEFAULT_PROFILE_DIR

log = logging.getLogger("linkedin_mcp.browser.auth")


def has_valid_session(profile_dir: Path) -> bool:
    """True if the profile dir has a storage_state.json with a li_at cookie.

    We check for li_at because it's the most reliable indicator that the
    LinkedIn session is established. Other cookies (JSESSIONID, etc.) are
    also present but li_at is the canonical one.
    """
    state_file = profile_dir / "storage_state.json"
    if not state_file.exists():
        return False
    try:
        state = json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    for cookie in state.get("cookies", []):
        if cookie.get("name") == "li_at" and cookie.get("value"):
            return True
    return False


async def ensure_session(cfg: Config) -> bool:
    """Verify the profile dir has a valid LinkedIn session.

    Returns True if session is good, False otherwise. The session is created
    by running ``linkedin-mcp login`` once; we do NOT do interactive
    password login from this function (use ``interactive_login()`` for that).
    """
    profile = Path(cfg.storage.browser_profile_dir)
    if not profile.exists():
        log.error(
            "No browser profile at %s. Run `linkedin-mcp login` first.",
            profile,
        )
        return False

    if not has_valid_session(profile):
        log.error(
            "Profile exists at %s but no li_at cookie found. "
            "Re-run `linkedin-mcp login`.",
            profile,
        )
        return False

    log.info("Existing session found in %s", profile)
    return True


async def interactive_login(
    cfg: Config,
    *,
    open_browser_fn=None,
    wait_for_url_fn=None,
    prompt_fn=None,
) -> bool:
    """Open browser, user logs in manually, profile is saved.

    Args:
        cfg: Config object (uses cfg.storage.browser_profile_dir).
        open_browser_fn: Optional async callable to open the browser
            and return a "browser" object. If None, defaults to opening
            agent-browser via BrowserClient.
        wait_for_url_fn: Optional async callable that polls the current URL
            and returns it. If None, defaults to BrowserClient.get_url.
        prompt_fn: Optional blocking callable that prompts the user. If None,
            uses input() (works for interactive terminals).

    Returns:
        True if login succeeded (URL reached /feed/ within timeout).
        False otherwise.

    The function:
        1. Creates the profile directory if missing.
        2. Opens a browser window to https://www.linkedin.com/login
        3. Polls the URL until it contains "/feed/" (or user confirms).
        4. Returns True/False based on outcome.

    The user does the actual login (email + password + 2FA) in the
    browser window. We do not auto-fill credentials.
    """
    profile = Path(cfg.storage.browser_profile_dir)
    profile.mkdir(parents=True, exist_ok=True)

    if prompt_fn is None:
        prompt_fn = input
    if wait_for_url_fn is None or open_browser_fn is None:
        # Lazy import to avoid pulling agent-browser at module load.
        from .client import BrowserClient
        from ..db import DB
        from ..safety import SafetyGuard
        from ..config import load_config

        # Reload config + db + guard if not provided
        if not cfg.li_at:
            cfg = load_config()
        db = DB(cfg.storage.db_path)
        guard = SafetyGuard(cfg, db)

        async with BrowserClient(cfg, db, guard) as br:
            return await _run_interactive_login(
                br, profile, prompt_fn=prompt_fn, wait_for_url_fn=wait_for_url_fn
            )

    # External browser was provided (used for testing)
    br = await open_browser_fn()
    return await _run_interactive_login(
        br, profile, prompt_fn=prompt_fn, wait_for_url_fn=wait_for_url_fn
    )


async def _run_interactive_login(
    br,
    profile: Path,
    *,
    prompt_fn,
    wait_for_url_fn=None,
    timeout_seconds: int = 300,
) -> bool:
    """Inner loop: open login page, wait for /feed/."""
    # The login page itself is at /login, which the challenge detector
    # normally treats as a security signal. We catch that here because
    # for the LOGIN flow, being on /login is the expected initial state.
    try:
        await br.navigate("https://www.linkedin.com/login")
    except Exception as e:
        print(f"❌ Could not open login page: {e}", file=sys.stderr)
        return False

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Browser opened to LinkedIn login page.", file=sys.stderr)
    print(f"Profile directory: {profile}", file=sys.stderr)
    print(f"", file=sys.stderr)
    print(f"1. Log in normally (email + password + 2FA if needed).", file=sys.stderr)
    print(f"2. Wait until you reach https://www.linkedin.com/feed/", file=sys.stderr)
    print(f"3. Come back to this terminal and press ENTER.", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    # Optional: poll the URL automatically
    if wait_for_url_fn is not None:
        import asyncio
        start = asyncio.get_event_loop().time()
        while True:
            try:
                url = await wait_for_url_fn()
            except Exception as e:
                log.debug("URL poll error: %s", e)
                url = ""
            if "/feed/" in url:
                print(f"\n✓ Detected login complete: {url}", file=sys.stderr)
                return True
            if asyncio.get_event_loop().time() - start > timeout_seconds:
                print(f"\n⏰ Timeout ({timeout_seconds}s) waiting for login.", file=sys.stderr)
                return False
            await asyncio.sleep(2)

    # Manual: user presses ENTER
    prompt_fn(
        "Press ENTER here once you're on the feed (or Ctrl+C to cancel): "
    )

    # Verify
    try:
        url = await br.get_url()
    except Exception as e:
        print(f"❌ Could not read browser URL: {e}", file=sys.stderr)
        return False

    if "/feed/" not in url:
        print(f"❌ URL is {url}, expected /feed/.", file=sys.stderr)
        print(f"   If you are logged in, the URL might be different (e.g. /mynetwork).", file=sys.stderr)
        return False

    print(f"✓ Login successful! URL: {url}", file=sys.stderr)
    print(f"  Profile saved at: {profile}", file=sys.stderr)
    return True
