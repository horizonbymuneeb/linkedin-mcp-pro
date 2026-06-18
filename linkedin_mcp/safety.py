"""Safety layer for linkedin-mcp-pro.

Wraps every write action in pre-flight checks:
  - Quota: not exceeding daily cap (with warm-up logic)
  - Business hours: only run during configured window
  - Rate limit: 429-aware backoff, auto-pause on repeat
  - Captcha: never auto-resolve, surface to user
  - Audit: every action logged with dry_run flag

This is the single place where ban-prevention lives. All MCP tools go
through `enforce()` before doing real work.
"""

from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from .config import Config
from .db import DB

log = logging.getLogger("linkedin_mcp.safety")

_DAY_MAP = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3,
    "fri": 4, "sat": 5, "sun": 6,
}


class SafetyError(Exception):
    """Base for safety rejections."""

    def __init__(self, reason: str, *, retry_after_seconds: Optional[int] = None):
        self.reason = reason
        self.retry_after_seconds = retry_after_seconds
        super().__init__(reason)


class QuotaExceededError(SafetyError):
    pass


class OutsideBusinessHoursError(SafetyError):
    pass


class RateLimitedError(SafetyError):
    pass


class CaptchaDetectedError(SafetyError):
    pass


class DryRun(SafetyError):
    """Raised in dry-run mode to signal 'would have done X, but skipping'."""

    def __init__(self, plan: str):
        self.plan = plan
        super().__init__(f"[DRY RUN] {plan}")


# Patterns that indicate LinkedIn served a challenge
_CAPTCHA_PATTERNS = [
    re.compile(r"captcha", re.I),
    re.compile(r"challenge.{0,40}verification", re.I),
    re.compile(r"verify.{0,30}human", re.I),
    re.compile(r"unusual.{0,30}activity", re.I),
    re.compile(r"please.complete.a.security.check", re.I),
    re.compile(r"checkpoint.{0,20}required", re.I),
]


@dataclass
class ActionPlan:
    """A proposed write action, with safety metadata."""

    action: str
    target: str
    payload: dict[str, Any]
    dry_run: bool = False


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _is_in_business_hours(cfg: Config, now: datetime | None = None) -> bool:
    """Check if given UTC time is within configured business window.

    NOTE: business hours are interpreted in UTC, not local. If you need
    local time, convert BUSINESS_HOURS_START/END in your .env.
    """
    now = now or _now_utc()
    day_ok = _DAY_MAP.get({0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri",
                            5: "sat", 6: "sun"}[now.weekday()]) in [
        _DAY_MAP[d] for d in cfg.safety.business_days
    ]
    if not day_ok:
        return False
    hour_ok = cfg.safety.business_hours_start <= now.hour < cfg.safety.business_hours_end
    return hour_ok


def next_business_window_open(cfg: Config) -> int:
    """Return seconds until the next business-hours window opens. 0 if open now."""
    if _is_in_business_hours(cfg):
        return 0
    # Compute naive "next open" — caller can use this for queueing
    return 60 * 30  # placeholder: recheck in 30 min
    # TODO: precise next-open calculation. For now, callers should re-poll.


def jitter_seconds(cfg: Config) -> float:
    """Return a random delay in [min, max] seconds to wait before the action."""
    lo = cfg.safety.action_jitter_min_seconds
    hi = cfg.safety.action_jitter_max_seconds
    return random.uniform(lo, hi)


def detect_captcha_in_text(text: str) -> bool:
    """True if the given HTML/text looks like a captcha / challenge page."""
    if not text:
        return False
    return any(p.search(text) for p in _CAPTCHA_PATTERNS)


class SafetyGuard:
    """Stateless wrapper that holds (Config, DB) and exposes enforce()."""

    def __init__(self, cfg: Config, db: DB):
        self.cfg = cfg
        self.db = db

    def enforce(self, plan: ActionPlan) -> None:
        """Pre-flight check. Raises on rejection. NO-OP if all clear.

        In dry-run mode, raises DryRun after recording the would-be action.
        """
        if plan.dry_run:
            # Log what would happen, then raise so caller stops before real work
            self.db.audit(
                plan.action,
                "dry_run",
                target=plan.target,
                dry_run=True,
                detail=plan.payload,
            )
            pretty = f"would {plan.action} {plan.target} with {plan.payload}"
            raise DryRun(pretty)

        # 1. Business hours
        if not _is_in_business_hours(self.cfg):
            self.db.audit(plan.action, "blocked_safety", target=plan.target,
                          detail={"reason": "outside_business_hours"})
            raise OutsideBusinessHoursError(
                f"Action blocked: outside business hours "
                f"({self.cfg.safety.business_hours_start:02d}:00-"
                f"{self.cfg.safety.business_hours_end:02d}:00 UTC, "
                f"{', '.join(self.cfg.safety.business_days)})"
            )

        # 2. Check for hard pause (captcha or 429 auto-pause)
        paused, remaining = self.writes_paused()
        if paused:
            self.db.audit(plan.action, "blocked_safety", target=plan.target,
                          detail={"reason": "writes_paused", "retry_in_sec": remaining})
            raise RateLimitedError(
                f"Action blocked: writes paused for {remaining}s (captcha or 429)",
                retry_after_seconds=remaining,
            )

        # 2. Quota check (with warm-up)
        limit = self._effective_limit(plan.action)
        q = self.db.get_quota(plan.action, limit=limit)
        if q.used >= q.limit:
            self.db.audit(plan.action, "blocked_safety", target=plan.target,
                          detail={"reason": "quota_exceeded", "used": q.used, "limit": q.limit})
            raise QuotaExceededError(
                f"Action blocked: {plan.action} quota exhausted "
                f"({q.used}/{q.limit} today). Try tomorrow."
            )

        # 3. Recent 429 / rate-limit backoff
        last_429 = self.db.get_state("last_429_at")
        if last_429:
            elapsed = time.time() - float(last_429)
            backoff = self.cfg.safety.rate_limit_backoff_base ** self._consecutive_429s()
            if elapsed < backoff * 60:
                wait = int(backoff * 60 - elapsed)
                self.db.audit(plan.action, "rate_limited", target=plan.target,
                              detail={"reason": "backoff_active", "retry_in_sec": wait})
                raise RateLimitedError(
                    f"Action blocked: in 429 backoff, retry in {wait}s",
                    retry_after_seconds=wait,
                )

    def record_success(self, plan: ActionPlan, result: Optional[dict] = None) -> None:
        """After successful execution: bump quota, log audit, clear 429 if any."""
        self.db.increment_quota(plan.action)
        self.db.audit(plan.action, "success", target=plan.target, detail=result)
        # If we just had a 429 and this succeeded, reset the counter
        if self.db.get_state("last_429_at"):
            self.db.set_state("last_429_at", "")
            self.db.set_state("consecutive_429s", "0")
        log.info("action=%s target=%s status=success", plan.action, plan.target)

    def record_failure(
        self, plan: ActionPlan, error: str, status: str = "failed"
    ) -> None:
        """Record a failed action. Does NOT increment quota (no real action taken)."""
        self.db.audit(plan.action, status, target=plan.target, detail={"error": error})
        log.warning("action=%s target=%s status=%s error=%s",
                    plan.action, plan.target, status, error)

    def record_429(self) -> None:
        """Called when an upstream 429 is observed. Bumps backoff state."""
        prev = int(self.db.get_state("consecutive_429s") or "0")
        self.db.set_state("consecutive_429s", str(prev + 1))
        self.db.set_state("last_429_at", str(time.time()))
        if self.cfg.safety.rate_limit_auto_pause:
            # Pause all writes for 1 hour
            self.db.set_state("writes_paused_until", str(time.time() + 3600))
        log.warning("429 received, backoff state updated (count=%d)", prev + 1)

    def record_captcha(self, plan: ActionPlan) -> None:
        """Surface captcha to user. Pause writes for 24h, alert if configured."""
        self.db.audit(plan.action, "failed", target=plan.target,
                      detail={"error": "captcha_detected"})
        # Hard pause: 24h
        self.db.set_state("writes_paused_until", str(time.time() + 86400))
        self.db.set_state("captcha_detected_at", str(time.time()))
        log.error("CAPTCHA detected on action=%s — writes paused 24h", plan.action)

    def writes_paused(self) -> tuple[bool, int]:
        """Return (paused, seconds_remaining)."""
        until = self.db.get_state("writes_paused_until")
        if not until:
            return False, 0
        try:
            t = float(until)
        except ValueError:
            return False, 0
        remaining = int(t - time.time())
        if remaining <= 0:
            return False, 0
        return True, remaining

    def _consecutive_429s(self) -> int:
        try:
            return int(self.db.get_state("consecutive_429s") or "0")
        except ValueError:
            return 0

    def _effective_limit(self, action: str) -> int:
        # Account age (in weeks) — defaults to 0 (no warmup) unless DB has data
        age_weeks = 0
        try:
            v = self.db.get_state("account_age_weeks")
            if v:
                age_weeks = int(v)
        except ValueError:
            age_weeks = 0
        return self.cfg.safety.effective_daily_limit(action, age_weeks)


def wait_with_jitter(cfg: Config) -> float:
    """Sleep for a jitter duration. Returns the seconds slept."""
    s = jitter_seconds(cfg)
    log.debug("jitter sleep: %.0fs", s)
    time.sleep(s)
    return s
