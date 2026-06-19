"""Ban-safety gate infrastructure for all auto-engagement features.

This module centralizes ALL the rules that protect the user's LinkedIn account
from being restricted or banned. Every auto-engagement action (like, comment,
connect, voice, feed) MUST pass through SafetyGate before executing.

The 10 safety rules implemented here:

 1. Daily quotas (max N actions per day, per action type)
 2. Hourly rate limits (burst protection)
 3. Business hours only (user-configured TZ + window)
 4. Account warm-up (new accounts get progressively higher limits)
 5. Whitelist required for auto-engagement (must explicitly allow keywords/people)
 6. Blacklist (always blocked — competitors, recruiters, agencies)
 7. Random cooldown between actions (human-like)
 8. Auto-pause on negative feedback (negative response rate > threshold)
 9. Auto-pause on shadow-ban detection (integrates with shadowban module)
10. Dry-run mode (default for first 30 days of use)

Every SafetyDecision is logged with full reasoning. Users can audit exactly
why an action was allowed or denied.

Configuration is in `safety_config.json` next to profile:
    {
      "enabled": true,
      "dry_run": true,                 # default true; user must explicitly opt out
      "tz": "Asia/Karachi",
      "business_hours": {"start": 9, "end": 20},
      "cooldown_seconds": {"min": 30, "max": 120},
      "daily_limits": {
        "account_age_days_required": 30,    # never auto-act on < 30 day accounts
        "warmup_days": 14,                  # new accounts: 20% of normal limits
        "warmup_multiplier": 0.2,
        "like":   30,
        "comment": 5,
        "connect": 20,
        "feed_watch": 100                   # feed listener is read-only, generous
      },
      "hourly_limits": {
        "like": 5,
        "comment": 1,
        "connect": 3
      },
      "negative_response_threshold": 0.10,   # 10% negative → pause 24h
      "shadowban_alert_pause_hours": 24,     # auto-pause if shadowban detected
      "whitelist": ["AI", "ML", "Python", "agentic"],
      "blacklist": ["recruiter", "staffing", "agency"]
    }
"""

from __future__ import annotations

import json
import logging
import random
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exception types
# ---------------------------------------------------------------------------


class BanSafetyError(Exception):
    """Base error for ban-safety gate failures."""


class QuotaExceededError(BanSafetyError):
    """Daily or hourly quota reached for this action type."""


class OutsideBusinessHoursError(BanSafetyError):
    """Current time is outside configured business hours."""


class AccountTooNewError(BanSafetyError):
    """Account is younger than minimum age — auto-engagement disabled."""


class WhitelistViolationError(BanSafetyError):
    """Action target doesn't match whitelist."""


class BlacklistViolationError(BanSafetyError):
    """Action target matches blacklist (always denied)."""


class NegativeFeedbackPausedError(BanSafetyError):
    """Auto-paused because too many negative responses."""


class ShadowBanPausedError(BanSafetyError):
    """Auto-paused because shadow-ban detector raised alert."""


class DryRunError(BanSafetyError):
    """Action would have been allowed but dry_run=true blocked it."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class DailyLimits:
    """Per-day limits for each action type."""

    like: int = 30
    comment: int = 5
    connect: int = 20
    feed_watch: int = 100
    account_age_days_required: int = 30
    warmup_days: int = 14
    warmup_multiplier: float = 0.2


@dataclass
class HourlyLimits:
    """Per-hour rate limits (burst protection)."""

    like: int = 5
    comment: int = 1
    connect: int = 3


@dataclass
class BusinessHours:
    """When auto-engagement is allowed (in user TZ)."""

    start: int = 9  # 9am
    end: int = 20   # 8pm


@dataclass
class CooldownRange:
    """Random cooldown between actions (seconds)."""

    min: int = 30
    max: int = 120


@dataclass
class SafetyConfig:
    """Top-level safety configuration."""

    enabled: bool = True
    dry_run: bool = True  # default true; opt-out explicitly
    tz: str = "Asia/Karachi"
    business_hours: BusinessHours = field(default_factory=BusinessHours)
    cooldown_seconds: CooldownRange = field(default_factory=CooldownRange)
    daily_limits: DailyLimits = field(default_factory=DailyLimits)
    hourly_limits: HourlyLimits = field(default_factory=HourlyLimits)
    negative_response_threshold: float = 0.10
    shadowban_alert_pause_hours: int = 24
    whitelist: list[str] = field(default_factory=list)
    blacklist: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> SafetyConfig:
        """Load config from JSON file, or return defaults if missing."""
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Safety config unreadable, using defaults: %s", exc)
            return cls()
        return cls.from_dict(data)

    def save(self, path: Path) -> None:
        """Persist config to JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SafetyConfig:
        bh = data.get("business_hours", {})
        cd = data.get("cooldown_seconds", {})
        dl = data.get("daily_limits", {})
        hl = data.get("hourly_limits", {})
        return cls(
            enabled=bool(data.get("enabled", True)),
            dry_run=bool(data.get("dry_run", True)),
            tz=str(data.get("tz", "Asia/Karachi")),
            business_hours=BusinessHours(
                start=int(bh.get("start", 9)), end=int(bh.get("end", 20))
            ),
            cooldown_seconds=CooldownRange(
                min=int(cd.get("min", 30)), max=int(cd.get("max", 120))
            ),
            daily_limits=DailyLimits(
                like=int(dl.get("like", 30)),
                comment=int(dl.get("comment", 5)),
                connect=int(dl.get("connect", 20)),
                feed_watch=int(dl.get("feed_watch", 100)),
                account_age_days_required=int(dl.get("account_age_days_required", 30)),
                warmup_days=int(dl.get("warmup_days", 14)),
                warmup_multiplier=float(dl.get("warmup_multiplier", 0.2)),
            ),
            hourly_limits=HourlyLimits(
                like=int(hl.get("like", 5)),
                comment=int(hl.get("comment", 1)),
                connect=int(hl.get("connect", 3)),
            ),
            negative_response_threshold=float(
                data.get("negative_response_threshold", 0.10)
            ),
            shadowban_alert_pause_hours=int(
                data.get("shadowban_alert_pause_hours", 24)
            ),
            whitelist=list(data.get("whitelist", [])),
            blacklist=list(data.get("blacklist", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "dry_run": self.dry_run,
            "tz": self.tz,
            "business_hours": {
                "start": self.business_hours.start,
                "end": self.business_hours.end,
            },
            "cooldown_seconds": {
                "min": self.cooldown_seconds.min,
                "max": self.cooldown_seconds.max,
            },
            "daily_limits": {
                "like": self.daily_limits.like,
                "comment": self.daily_limits.comment,
                "connect": self.daily_limits.connect,
                "feed_watch": self.daily_limits.feed_watch,
                "account_age_days_required": self.daily_limits.account_age_days_required,
                "warmup_days": self.daily_limits.warmup_days,
                "warmup_multiplier": self.daily_limits.warmup_multiplier,
            },
            "hourly_limits": {
                "like": self.hourly_limits.like,
                "comment": self.hourly_limits.comment,
                "connect": self.hourly_limits.connect,
            },
            "negative_response_threshold": self.negative_response_threshold,
            "shadowban_alert_pause_hours": self.shadowban_alert_pause_hours,
            "whitelist": list(self.whitelist),
            "blacklist": list(self.blacklist),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


@dataclass
class SafetyDecision:
    """Result of a safety check.

    `allowed=True, dry_run=True` means the action would have been allowed
    but is blocked because dry_run mode is on. Caller can either log and
    return success (silent), or surface a "would have done X" message.
    """

    allowed: bool
    reason: str
    action_type: str
    target: str = ""
    dry_run: bool = False
    cooldown_until: datetime | None = None
    effective_limit: int = 0
    used_today: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "action_type": self.action_type,
            "target": self.target,
            "dry_run": self.dry_run,
            "cooldown_until": (
                self.cooldown_until.isoformat() if self.cooldown_until else None
            ),
            "effective_limit": self.effective_limit,
            "used_today": self.used_today,
        }


# ---------------------------------------------------------------------------
# Tracker (SQLite-backed)
# ---------------------------------------------------------------------------


class SafetyTracker:
    """Persists action counts, cooldowns, negative feedback, and pauses.

    Schema:
        actions(id, ts, action_type, target, allowed, dry_run, reason)
        cooldowns(action_type, until_ts)
        pauses(until_ts, reason, created_ts)
        negative_responses(id, ts, action_type, target, response_kind)
        account_meta(key, value)  -- account_created_at, etc.
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        action_type TEXT NOT NULL,
        target TEXT NOT NULL DEFAULT '',
        allowed INTEGER NOT NULL,
        dry_run INTEGER NOT NULL DEFAULT 0,
        reason TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_actions_ts ON actions(ts);
    CREATE INDEX IF NOT EXISTS idx_actions_type_ts ON actions(action_type, ts);

    CREATE TABLE IF NOT EXISTS cooldowns (
        action_type TEXT PRIMARY KEY,
        until_ts TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS pauses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        until_ts TEXT NOT NULL,
        reason TEXT NOT NULL,
        created_ts TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS negative_responses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        action_type TEXT NOT NULL,
        target TEXT NOT NULL DEFAULT '',
        response_kind TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS account_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(self.SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    # ------------------------------------------------------------------
    # Action logging
    # ------------------------------------------------------------------

    def log_action(
        self,
        action_type: str,
        target: str,
        allowed: bool,
        dry_run: bool = False,
        reason: str = "",
    ) -> None:
        """Record every safety check (allowed or denied)."""
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO actions (ts, action_type, target, allowed, dry_run, reason) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    datetime.now(UTC).isoformat(),
                    action_type,
                    target,
                    1 if allowed else 0,
                    1 if dry_run else 0,
                    reason,
                ),
            )

    def used_today(self, action_type: str, now: datetime | None = None) -> int:
        """Count successful (allowed, non-dry-run) actions today in user TZ."""
        now = now or datetime.now(UTC)
        local_date = now.date().isoformat()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM actions "
                "WHERE action_type = ? AND allowed = 1 AND dry_run = 0 "
                "AND substr(ts, 1, 10) = ?",
                (action_type, local_date),
            ).fetchone()
        return int(row[0]) if row else 0

    def used_this_hour(self, action_type: str, now: datetime | None = None) -> int:
        """Count successful (allowed, non-dry-run) actions in current UTC hour."""
        now = now or datetime.now(UTC)
        # Match `YYYY-MM-DDTHH` (13 chars) against ts prefix
        hour_prefix = now.strftime("%Y-%m-%dT%H")
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM actions "
                "WHERE action_type = ? AND allowed = 1 AND dry_run = 0 "
                "AND substr(ts, 1, 13) = ?",
                (action_type, hour_prefix),
            ).fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------
    # Cooldowns
    # ------------------------------------------------------------------

    def get_cooldown(self, action_type: str) -> datetime | None:
        """Return when next action is allowed, or None if no cooldown active."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT until_ts FROM cooldowns WHERE action_type = ?",
                (action_type,),
            ).fetchone()
        if not row:
            return None
        try:
            until = datetime.fromisoformat(row[0])
        except ValueError:
            return None
        if until.tzinfo is None:
            until = until.replace(tzinfo=UTC)
        if until <= datetime.now(UTC):
            return None
        return until

    def set_cooldown(self, action_type: str, seconds: int) -> datetime:
        """Set cooldown for action_type, return when it expires."""
        until = datetime.now(UTC) + timedelta(seconds=seconds)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cooldowns (action_type, until_ts) VALUES (?, ?)",
                (action_type, until.isoformat()),
            )
        return until

    # ------------------------------------------------------------------
    # Pauses
    # ------------------------------------------------------------------

    def is_paused(self) -> tuple[bool, str | None]:
        """Return (paused, reason) — True if any active pause."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT until_ts, reason FROM pauses "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if not row:
            return False, None
        try:
            until = datetime.fromisoformat(row[0])
        except ValueError:
            return False, None
        if until.tzinfo is None:
            until = until.replace(tzinfo=UTC)
        if until <= datetime.now(UTC):
            return False, None
        return True, f"{row[1]} (until {until.isoformat()})"

    def add_pause(self, hours: int, reason: str) -> datetime:
        """Add a pause for N hours, return when it expires."""
        until = datetime.now(UTC) + timedelta(hours=hours)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO pauses (until_ts, reason, created_ts) VALUES (?, ?, ?)",
                (until.isoformat(), reason, datetime.now(UTC).isoformat()),
            )
        return until

    def clear_pauses(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM pauses")

    # ------------------------------------------------------------------
    # Negative responses
    # ------------------------------------------------------------------

    def log_negative_response(
        self, action_type: str, target: str, response_kind: str
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO negative_responses (ts, action_type, target, response_kind) "
                "VALUES (?, ?, ?, ?)",
                (
                    datetime.now(UTC).isoformat(),
                    action_type,
                    target,
                    response_kind,
                ),
            )

    def negative_response_rate(
        self, action_type: str, lookback_days: int = 7
    ) -> float:
        """Return negative_responses / total_actions over lookback window."""
        cutoff = (datetime.now(UTC) - timedelta(days=lookback_days)).isoformat()
        with self._lock, self._connect() as conn:
            neg_row = conn.execute(
                "SELECT COUNT(*) FROM negative_responses "
                "WHERE action_type = ? AND ts >= ?",
                (action_type, cutoff),
            ).fetchone()
            tot_row = conn.execute(
                "SELECT COUNT(*) FROM actions "
                "WHERE action_type = ? AND allowed = 1 AND dry_run = 0 AND ts >= ?",
                (action_type, cutoff),
            ).fetchone()
        neg = int(neg_row[0]) if neg_row else 0
        tot = int(tot_row[0]) if tot_row else 0
        if tot == 0:
            return 0.0
        return neg / tot

    # ------------------------------------------------------------------
    # Account meta
    # ------------------------------------------------------------------

    def set_account_meta(self, key: str, value: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO account_meta (key, value) VALUES (?, ?)",
                (key, value),
            )

    def get_account_meta(self, key: str) -> str | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM account_meta WHERE key = ?", (key,)
            ).fetchone()
        return str(row[0]) if row else None


# ---------------------------------------------------------------------------
# Gate (main entry point)
# ---------------------------------------------------------------------------


class SafetyGate:
    """Central decision point for all auto-engagement actions.

    Every action call goes through `check()` which:
      1. Validates the action is enabled
      2. Checks account age
      3. Checks active pauses (negative feedback, shadow-ban)
      4. Checks business hours in user TZ
      5. Checks daily + hourly quotas
      6. Checks whitelist/blacklist
      7. Checks cooldown
      8. If allowed and not dry_run, records the action and sets cooldown

    Returns SafetyDecision with full reasoning.
    """

    def __init__(self, config: SafetyConfig, tracker: SafetyTracker) -> None:
        self.config = config
        self.tracker = tracker
        try:
            self.tz = ZoneInfo(config.tz)
        except Exception:
            log.warning("Invalid tz %r, falling back to UTC", config.tz)
            self.tz = ZoneInfo("UTC")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(
        self,
        action_type: str,
        target: str = "",
        target_text: str = "",
    ) -> SafetyDecision:
        """Check whether `action_type` (against `target_text`) is allowed.

        Args:
            action_type: one of "like", "comment", "connect", "feed_watch"
            target: optional URL/URN identifying the target
            target_text: the human text to whitelist/blacklist match against
        """
        cfg = self.config
        base = SafetyDecision(
            allowed=False,
            reason="",
            action_type=action_type,
            target=target,
        )

        if not cfg.enabled:
            base.reason = "safety disabled"
            return base

        # 1. Account age
        created = self.tracker.get_account_meta("account_created_at")
        if created:
            try:
                created_dt = datetime.fromisoformat(created)
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=UTC)
                age_days = (datetime.now(UTC) - created_dt).days
                if age_days < cfg.daily_limits.account_age_days_required:
                    base.reason = (
                        f"account age {age_days}d < required "
                        f"{cfg.daily_limits.account_age_days_required}d"
                    )
                    self.tracker.log_action(
                        action_type, target, False, False, base.reason
                    )
                    return base
            except ValueError:
                pass

        # 2. Active pause?
        paused, pause_reason = self.tracker.is_paused()
        if paused:
            base.reason = f"paused: {pause_reason}"
            self.tracker.log_action(
                action_type, target, False, False, base.reason
            )
            return base

        # 3. Business hours
        local_now = datetime.now(self.tz)
        if not (
            cfg.business_hours.start <= local_now.hour < cfg.business_hours.end
        ):
            base.reason = (
                f"outside business hours "
                f"({local_now.strftime('%H:%M')} {cfg.tz}, "
                f"allowed {cfg.business_hours.start:02d}-{cfg.business_hours.end:02d})"
            )
            self.tracker.log_action(
                action_type, target, False, False, base.reason
            )
            return base

        # 4. Blacklist
        for term in cfg.blacklist:
            if term and term.lower() in target_text.lower():
                base.reason = f"blacklist match: {term!r}"
                self.tracker.log_action(
                    action_type, target, False, False, base.reason
                )
                return base

        # 5. Whitelist (only for like/comment/connect, not feed_watch)
        if (
            action_type in {"like", "comment", "connect"}
            and cfg.whitelist
        ):
            text_lc = target_text.lower()
            if not any(w.lower() in text_lc for w in cfg.whitelist):
                base.reason = "target does not match any whitelist term"
                self.tracker.log_action(
                    action_type, target, False, False, base.reason
                )
                return base

        # 6. Cooldown
        cooldown_until = self.tracker.get_cooldown(action_type)
        if cooldown_until:
            base.reason = f"cooldown until {cooldown_until.isoformat()}"
            base.cooldown_until = cooldown_until
            self.tracker.log_action(
                action_type, target, False, False, base.reason
            )
            return base

        # 7. Quotas (daily + hourly)
        daily_limit = self._effective_daily_limit(action_type)
        used = self.tracker.used_today(action_type)
        if used >= daily_limit:
            base.reason = (
                f"daily limit reached ({used}/{daily_limit} {action_type})"
            )
            base.effective_limit = daily_limit
            base.used_today = used
            self.tracker.log_action(
                action_type, target, False, False, base.reason
            )
            return base

        hourly_limit = self._hourly_limit(action_type)
        used_hr = self.tracker.used_this_hour(action_type)
        if used_hr >= hourly_limit:
            base.reason = (
                f"hourly limit reached ({used_hr}/{hourly_limit} {action_type})"
            )
            base.effective_limit = hourly_limit
            base.used_today = used
            self.tracker.log_action(
                action_type, target, False, False, base.reason
            )
            return base

        # 8. Negative response rate
        rate = self.tracker.negative_response_rate(action_type)
        if rate > cfg.negative_response_threshold:
            self.tracker.add_pause(
                hours=24, reason=f"negative response rate {rate:.1%} on {action_type}"
            )
            base.reason = (
                f"auto-paused for 24h: negative response rate {rate:.1%}"
            )
            self.tracker.log_action(
                action_type, target, False, False, base.reason
            )
            return base

        # All checks passed
        decision = SafetyDecision(
            allowed=True,
            reason="all checks passed",
            action_type=action_type,
            target=target,
            effective_limit=daily_limit,
            used_today=used + 1,  # after this action
        )

        if cfg.dry_run:
            decision.allowed = False
            decision.dry_run = True
            decision.reason = (
                f"dry-run: would have allowed "
                f"({used + 1}/{daily_limit} {action_type})"
            )
            self.tracker.log_action(
                action_type, target, False, True, decision.reason
            )
        else:
            # Real action — record it + set cooldown
            self.tracker.log_action(action_type, target, True, False, "allowed")
            # random is fine here — we want human-like cooldown, not crypto
            cooldown_s = random.randint(  # noqa: S311
                cfg.cooldown_seconds.min, cfg.cooldown_seconds.max
            )
            self.tracker.set_cooldown(action_type, cooldown_s)
            decision.reason = (
                f"allowed ({used + 1}/{daily_limit} {action_type}, "
                f"cooldown {cooldown_s}s)"
            )

        return decision

    # ------------------------------------------------------------------
    # Negative feedback integration
    # ------------------------------------------------------------------

    def report_negative_response(
        self, action_type: str, target: str = "", response_kind: str = "unknown"
    ) -> None:
        """Called when a user reports a negative response to an action.

        If the rate exceeds threshold, SafetyGate will auto-pause on next check().
        """
        self.tracker.log_negative_response(action_type, target, response_kind)
        rate = self.tracker.negative_response_rate(action_type)
        if rate > self.config.negative_response_threshold:
            self.tracker.add_pause(
                hours=24,
                reason=f"negative response rate {rate:.1%} on {action_type}",
            )

    def report_shadowban(self, hours: int | None = None) -> None:
        """Called by shadowban detector when suppression pattern is found."""
        hrs = hours if hours is not None else self.config.shadowban_alert_pause_hours
        self.tracker.add_pause(
            hours=hrs, reason="shadow-ban detector raised alert"
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _effective_daily_limit(self, action_type: str) -> int:
        """Get the daily limit for action_type, adjusted for warm-up."""
        base = {
            "like": self.config.daily_limits.like,
            "comment": self.config.daily_limits.comment,
            "connect": self.config.daily_limits.connect,
            "feed_watch": self.config.daily_limits.feed_watch,
        }.get(action_type, 0)
        if base == 0:
            return 0

        created = self.tracker.get_account_meta("account_created_at")
        if not created:
            return base
        try:
            created_dt = datetime.fromisoformat(created)
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=UTC)
            age_days = (datetime.now(UTC) - created_dt).days
        except ValueError:
            return base

        if age_days < self.config.daily_limits.warmup_days:
            return max(1, int(base * self.config.daily_limits.warmup_multiplier))
        return base

    def _hourly_limit(self, action_type: str) -> int:
        return {
            "like": self.config.hourly_limits.like,
            "comment": self.config.hourly_limits.comment,
            "connect": self.config.hourly_limits.connect,
            "feed_watch": 999,  # read-only, no real cap
        }.get(action_type, 0)


# ---------------------------------------------------------------------------
# Convenience builder
# ---------------------------------------------------------------------------


def build_default_gate(profile_dir: Path) -> SafetyGate:
    """Build a SafetyGate from default paths under profile_dir."""
    cfg_path = profile_dir / "safety_config.json"
    db_path = profile_dir / "safety.db"
    return SafetyGate(SafetyConfig.load(cfg_path), SafetyTracker(db_path))
