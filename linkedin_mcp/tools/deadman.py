"""MCP tools for the dead-man switch (v0.5.0).

Three tools, all metadata/notification-only — they query the local DB
and (optionally) hit the Telegram Bot API. None of them post to
LinkedIn, so they bypass the SafetyGuard.

Tools:
    deadman_status() -> dict
        Read-only check — returns the same dict as ``DeadManSwitch.check()``.

    deadman_check_and_alert() -> dict
        Force a check and, if status=='alert' and the 24h cooldown has
        elapsed, send a Telegram alert. Returns ``check`` merged with
        ``alert_sent: bool``.

    deadman_test_alert() -> dict
        Send a one-off test Telegram message (``force=True``) so the
        operator can verify the bot token + chat id are wired up.
        Returns ``{"sent": bool, "error": str | None}``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..config import load_config
from ..deadman import DeadManError, DeadManSwitch

log = logging.getLogger("linkedin_mcp.tools.deadman")


def _db_path() -> Path:
    """Resolve the DB path via the same loader the rest of the server uses."""
    try:
        cfg = load_config()
        return Path(cfg.storage.db_path)
    except Exception as e:
        log.warning("deadman tool: could not load config: %s — using ./data default", e)
        return Path("./data/linkedin-mcp-pro.db")


def deadman_status() -> dict[str, Any]:
    """Return the current dead-man status (no side effects, no alert)."""
    with DeadManSwitch(_db_path()) as sw:
        return sw.check()


def deadman_check_and_alert() -> dict[str, Any]:
    """Force a check, then send a Telegram alert if warranted.

    Returns the check dict augmented with ``alert_sent`` (bool) and
    ``alert_error`` (str | None).  ``alert_sent=True`` means the
    message was delivered to Telegram (HTTP 2xx).
    """
    db_path = _db_path()
    sw = DeadManSwitch(db_path)
    try:
        result = sw.check()
        if not result.get("should_alert"):
            return result | {"alert_sent": False, "alert_error": None}

        sent = sw.send_alert(
            days_since=result.get("days_since"),
            last_post_at=result.get("last_post_at"),
            force=False,
        )
        return result | {
            "alert_sent": sent,
            "alert_error": None if sent else "telegram_send_failed",
        }
    except DeadManError as e:
        # Bad config (e.g. threshold < 1) — surface a clean error dict.
        return {
            "error": str(e),
            "alert_sent": False,
        }
    finally:
        sw.close()


def deadman_test_alert() -> dict[str, Any]:
    """Send a test Telegram message (``force=True`` — bypasses 24h cooldown)."""
    db_path = _db_path()
    sw = DeadManSwitch(db_path)
    try:
        sent = sw.send_alert(force=True, kind="test")
    except DeadManError as e:
        return {"sent": False, "error": str(e)}
    finally:
        sw.close()
    return {"sent": sent, "error": None if sent else "telegram_send_failed"}


__all__ = [
    "deadman_status",
    "deadman_check_and_alert",
    "deadman_test_alert",
]
