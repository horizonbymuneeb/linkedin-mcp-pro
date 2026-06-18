"""Dead-man switch for linkedin-mcp-pro (v0.5.0).

If the user stops posting for N days (default 3), sends a Telegram
alert. The check itself is cheap and offline (just queries the audit
log), so it's safe to run on a daily systemd timer.

Status logic (where T = threshold in days):
    days_since <  T - 1   → 'ok'       (well within budget)
    T - 1 <= days_since < T  → 'warning'  (one day from alerting)
    days_since >= T       → 'alert'     (threshold breached)

Alert spam control: at most one alert per 24h, tracked in
``session_state`` key ``deadman.last_alert_sent_at``.

Telegram delivery uses ``urllib`` from the stdlib (no extra deps). If
``LINKEDIN_MCP_TELEGRAM_BOT_TOKEN`` or ``LINKEDIN_MCP_TELEGRAM_CHAT_ID``
are not set, ``send_alert()`` returns ``False`` and writes a warning to
the audit log instead of crashing.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

from .db import DB

log = logging.getLogger("linkedin_mcp.deadman")

# --- constants ---------------------------------------------------------------

ENV_THRESHOLD_DAYS = "LINKEDIN_MCP_DEADMAN_THRESHOLD_DAYS"
ENV_BOT_TOKEN = "LINKEDIN_MCP_TELEGRAM_BOT_TOKEN"
ENV_CHAT_ID = "LINKEDIN_MCP_TELEGRAM_CHAT_ID"

DEFAULT_THRESHOLD_DAYS = 3
TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_TIMEOUT_SECONDS = 10
ALERT_COOLDOWN_HOURS = 24

# session_state key used to throttle alerts.
_LAST_ALERT_KEY = "deadman.last_alert_sent_at"
_THRESHOLD_STATE_KEY = "deadman.threshold_days"


# --- exceptions --------------------------------------------------------------


class DeadManError(RuntimeError):
    """Raised for unrecoverable dead-man switch failures (DB, bad config, etc)."""


# --- config helpers ----------------------------------------------------------


def _threshold_from_env(default: int = DEFAULT_THRESHOLD_DAYS) -> int:
    """Read the threshold from env, falling back to the default.

    A non-positive value is coerced to 1 (you can't alert in 0 days).
    """
    raw = os.environ.get(ENV_THRESHOLD_DAYS, "").strip()
    if not raw:
        return default
    try:
        val = int(raw)
    except ValueError:
        log.warning(
            "deadman: %s=%r is not an int; falling back to %d",
            ENV_THRESHOLD_DAYS, raw, default,
        )
        return default
    if val < 1:
        log.warning(
            "deadman: %s=%d must be >= 1; clamping to 1", ENV_THRESHOLD_DAYS, val
        )
        return 1
    return val


def _telegram_creds() -> tuple[Optional[str], Optional[str]]:
    """Return (bot_token, chat_id) from env, each possibly None."""
    token = os.environ.get(ENV_BOT_TOKEN, "").strip() or None
    chat_id = os.environ.get(ENV_CHAT_ID, "").strip() or None
    return token, chat_id


# --- main class --------------------------------------------------------------


@dataclass
class CheckResult:
    """Return type for ``DeadManSwitch.check()`` — kept as a dict in the public API."""

    last_post_at: Optional[str]
    days_since: Optional[float]
    status: str  # 'ok' | 'warning' | 'alert' | 'no_posts'
    should_alert: bool
    threshold_days: int
    last_alert_sent_at: Optional[str]
    alert_suppressed_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_post_at": self.last_post_at,
            "days_since": self.days_since,
            "status": self.status,
            "should_alert": self.should_alert,
            "threshold_days": self.threshold_days,
            "last_alert_sent_at": self.last_alert_sent_at,
            "alert_suppressed_reason": self.alert_suppressed_reason,
        }


class DeadManSwitch:
    """Tracks posting cadence and fires Telegram alerts on long silence.

    Threshold resolution order:
        1. ``threshold_days`` passed to the constructor
        2. ``session_state[deadman.threshold_days]`` (set via CLI ``set-threshold``)
        3. ``LINKEDIN_MCP_DEADMAN_THRESHOLD_DAYS`` env var
        4. ``DEFAULT_THRESHOLD_DAYS`` (3)
    """

    def __init__(
        self,
        db: Union[DB, Path, str],
        threshold_days: Optional[int] = None,
        now: Optional[datetime] = None,
    ):
        if isinstance(db, DB):
            self.db = db
            self._owns_db = False
        else:
            self.db = DB(Path(db))
            self._owns_db = True
        self._now = now  # injectable clock for tests
        # Stash the explicit override so _resolve_threshold can use it.
        self._explicit_threshold = threshold_days

    # -- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        if self._owns_db:
            self.db.close()

    def __enter__(self) -> "DeadManSwitch":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- threshold resolution ------------------------------------------------

    def _resolve_threshold(self) -> int:
        # 1) explicit constructor arg wins
        if self._explicit_threshold is not None and self._explicit_threshold >= 1:
            return int(self._explicit_threshold)
        # 2) persisted in DB (set via CLI set-threshold)
        try:
            stored = self.db.get_state(_THRESHOLD_STATE_KEY)
            if stored is not None:
                stored_int = int(stored)
                if stored_int >= 1:
                    return stored_int
        except (ValueError, TypeError):
            pass
        # 3) env, then default
        return _threshold_from_env()

    def set_threshold(self, days: int) -> int:
        """Persist a new threshold to ``session_state``. Returns the value stored.

        This is intentionally env-agnostic: the DB-stored value takes
        precedence over ``LINKEDIN_MCP_DEADMAN_THRESHOLD_DAYS`` for any
        caller that goes through ``get_threshold()`` / ``check()`` /
        ``send_alert()``. We deliberately do NOT mutate the process env
        — that would leak across tests and surprise unrelated callers.
        """
        if days < 1:
            raise DeadManError(f"threshold must be >= 1 day, got {days}")
        self.db.set_state(_THRESHOLD_STATE_KEY, str(int(days)))
        return int(days)

    def get_threshold(self) -> int:
        return self._resolve_threshold()

    # -- core checks ---------------------------------------------------------

    def _now_utc(self) -> datetime:
        if self._now is not None:
            return self._now
        return datetime.now(timezone.utc)

    def _last_successful_post(self) -> Optional[str]:
        """ISO timestamp of the most recent successful ``post`` action, or None."""
        rows = self.db.get_audit(action="post", limit=50)
        for r in rows:
            if r.get("status") == "success":
                return r.get("created_at")
        return None

    def _compute_status(self, days_since: Optional[float], threshold: int) -> str:
        if days_since is None:
            # No posts yet → treat as 'alert' once we have a threshold; caller
            # converts to 'no_posts' for the public API.
            return "alert"
        if days_since >= threshold:
            return "alert"
        if days_since >= threshold - 1:
            return "warning"
        return "ok"

    def _cooldown_active(self, now: datetime) -> bool:
        last = self.db.get_state(_LAST_ALERT_KEY)
        if not last:
            return False
        try:
            last_dt = datetime.fromisoformat(last)
        except ValueError:
            return False
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        elapsed = (now - last_dt).total_seconds()
        return elapsed < ALERT_COOLDOWN_HOURS * 3600

    def check(self) -> dict[str, Any]:
        """Inspect the audit log and decide if an alert is warranted.

        Returns a dict (matching ``CheckResult.to_dict()``) with keys:
            last_post_at, days_since, status, should_alert,
            threshold_days, last_alert_sent_at, alert_suppressed_reason.
        """
        threshold = self._resolve_threshold()
        now = self._now_utc()

        last_post_at = self._last_successful_post()
        last_alert_at = self.db.get_state(_LAST_ALERT_KEY)

        if last_post_at is None:
            return CheckResult(
                last_post_at=None,
                days_since=None,
                status="no_posts",
                should_alert=False,  # no posts ever — nothing to alarm about yet
                threshold_days=threshold,
                last_alert_sent_at=last_alert_at,
                alert_suppressed_reason="no_posts_yet",
            ).to_dict()

        try:
            last_dt = datetime.fromisoformat(last_post_at)
        except ValueError:
            log.warning("deadman: could not parse last_post_at=%r", last_post_at)
            return CheckResult(
                last_post_at=last_post_at,
                days_since=None,
                status="no_posts",
                should_alert=False,
                threshold_days=threshold,
                last_alert_sent_at=last_alert_at,
                alert_suppressed_reason="unparseable_timestamp",
            ).to_dict()
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)

        days_since = (now - last_dt).total_seconds() / 86400.0
        status = self._compute_status(days_since, threshold)

        should_alert = status == "alert"
        suppressed: Optional[str] = None
        if should_alert and self._cooldown_active(now):
            should_alert = False
            suppressed = "cooldown_24h"

        return CheckResult(
            last_post_at=last_post_at,
            days_since=days_since,
            status=status,
            should_alert=should_alert,
            threshold_days=threshold,
            last_alert_sent_at=last_alert_at,
            alert_suppressed_reason=suppressed,
        ).to_dict()

    # -- alerts --------------------------------------------------------------

    @staticmethod
    def format_message(
        days_since: Optional[float],
        threshold: int,
        last_post_at: Optional[str],
        kind: str = "alert",
    ) -> str:
        """Build the Markdown message body.

        ``kind`` is ``'alert'`` or ``'test'`` — controls the emoji + phrasing.
        """
        if kind == "test":
            return (
                "✅ *linkedin-mcp-pro — Dead-man test*\n\n"
                "This is a *test* alert. Your Telegram wiring is working.\n\n"
                f"_Threshold: {threshold} days._"
            )

        days_str = f"{days_since:.1f}" if days_since is not None else "n/a"
        last_str = last_post_at or "never"
        return (
            "🚨 *LinkedIn Dead-Man Switch*\n\n"
            f"*Days since last post:* {days_str}\n"
            f"*Threshold:* {threshold} days\n"
            f"*Last post:* {last_str}\n\n"
            "Account may be flagged or cookie expired. "
            "Run `linkedin-mcp deadman status` for details."
        )

    def send_alert(
        self,
        days_since: Optional[float] = None,
        last_post_at: Optional[str] = None,
        force: bool = False,
        kind: str = "alert",
    ) -> bool:
        """Send a Telegram alert. Returns True on success, False otherwise.

        - If Telegram env vars are missing: logs a warning + audit entry
          and returns False (never raises for missing config).
        - Honors the 24h cooldown unless ``force=True`` (used by the
          CLI ``test-alert`` subcommand).
        - HTTP/network errors are caught and logged, then returned as False.
        """
        threshold = self._resolve_threshold()
        if last_post_at is None or days_since is None:
            # Re-derive from check() if not provided.
            result = self.check()
            last_post_at = result["last_post_at"]
            days_since = result["days_since"]

        if not force and self._cooldown_active(self._now_utc()):
            log.info("deadman: alert suppressed (24h cooldown)")
            self._audit_misc("cooldown", {"kind": kind})
            return False

        token, chat_id = _telegram_creds()
        text = self.format_message(
            days_since=days_since,
            threshold=threshold,
            last_post_at=last_post_at,
            kind=kind,
        )

        if not token or not chat_id:
            warn = (
                f"Telegram not configured — would alert: "
                f"days_since={days_since}, threshold={threshold}"
            )
            log.warning("deadman: %s", warn)
            self._audit_misc(
                "telegram_unconfigured",
                {"message": warn, "kind": kind},
            )
            return False

        ok = self._send_telegram(token, chat_id, text)
        if ok and not force:
            # Only persist the cooldown for real alerts, not tests.
            self.db.set_state(_LAST_ALERT_KEY, self._now_utc().isoformat(timespec="seconds"))
            self._audit_misc(
                "alert_sent",
                {"days_since": days_since, "threshold": threshold, "kind": kind},
            )
        elif ok and force:
            self._audit_misc(
                "test_alert_sent",
                {"kind": kind, "threshold": threshold},
            )
        else:
            self._audit_misc(
                "alert_failed",
                {"kind": kind, "threshold": threshold},
            )
        return ok

    # -- internal helpers ----------------------------------------------------

    def _audit_misc(self, status: str, detail: dict[str, Any]) -> None:
        """Write a non-action audit row (we use action='deadman' to keep it separate)."""
        try:
            self.db.audit(
                action="deadman",
                status=status,
                target=None,
                dry_run=0,
                detail=detail,
            )
        except Exception as e:  # pragma: no cover - audit must never crash the caller
            log.warning("deadman: failed to write audit row: %s", e)

    @staticmethod
    def _send_telegram(bot_token: str, chat_id: str, text: str) -> bool:
        """POST to the Telegram Bot API. Returns True on 2xx, False otherwise."""
        url = TELEGRAM_URL.format(token=bot_token)
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": "true",
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=TELEGRAM_TIMEOUT_SECONDS) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                if 200 <= resp.status < 300:
                    log.info("deadman: telegram ok (%d): %s", resp.status, body[:200])
                    return True
                log.warning("deadman: telegram non-2xx %d: %s", resp.status, body[:200])
                return False
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:  # pragma: no cover
                pass
            log.warning("deadman: telegram HTTPError %d: %s", e.code, body[:200])
            return False
        except urllib.error.URLError as e:
            log.warning("deadman: telegram URLError: %s", e)
            return False
        except TimeoutError:
            log.warning("deadman: telegram timed out after %ds", TELEGRAM_TIMEOUT_SECONDS)
            return False
        except Exception as e:  # pragma: no cover - defensive
            log.warning("deadman: telegram unexpected error: %s", e)
            return False


# -- module-level convenience -------------------------------------------------


def quick_check(db_path: Union[DB, Path, str]) -> dict[str, Any]:
    """One-shot helper: build a switch, run check(), close the DB."""
    with DeadManSwitch(db_path) as sw:
        return sw.check()


__all__ = [
    "DeadManError",
    "DeadManSwitch",
    "CheckResult",
    "DEFAULT_THRESHOLD_DAYS",
    "ENV_BOT_TOKEN",
    "ENV_CHAT_ID",
    "ENV_THRESHOLD_DAYS",
    "ALERT_COOLDOWN_HOURS",
    "quick_check",
]
