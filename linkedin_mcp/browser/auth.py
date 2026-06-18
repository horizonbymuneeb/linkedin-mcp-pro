"""Auth / session bootstrap for the browser module.

LinkedIn's bot detection is more aggressive on cold sessions. We bootstrap
a valid session by injecting the ``li_at`` cookie into agent-browser's
profile dir, then verify on first navigation that we land on /feed/ —
not on /login, /checkpoint, or /authwall.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import Config

log = logging.getLogger("linkedin_mcp.browser.auth")


def has_valid_session(profile_dir: Path) -> bool:
    """True if the profile dir has a storage_state.json with a li_at cookie."""
    state_file = profile_dir / "storage_state.json"
    if not state_file.exists():
        return False
    try:
        import json
        state = json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    for cookie in state.get("cookies", []):
        if cookie.get("name") == "li_at" and cookie.get("value"):
            return True
    return False


async def ensure_session(cfg: Config) -> bool:
    """Verify the profile dir has a valid li_at session cookie.

    Returns True if session is good, False otherwise. We do NOT do interactive
    password login — user must provide a fresh li_at cookie if the old one
    expired.
    """
    if not cfg.li_at:
        log.error("No li_at configured. Set LI_AT or LI_AT_FILE in .env")
        return False

    profile = Path(cfg.storage.browser_profile_dir)
    if not has_valid_session(profile):
        log.info("No existing session in %s — will inject li_at on next BrowserClient init",
                 profile)
        return False

    log.info("Existing session found in %s", profile)
    return True
