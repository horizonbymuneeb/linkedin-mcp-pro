"""Post Analytics for linkedin-mcp-pro (v0.6.0).

Pure read-only over the existing ``audit_log`` and ``daily_quotas`` tables
— no new tables, no writes. All time math uses stdlib ``datetime``.

Public surface (one class, no state)::

    a = Analytics(db)
    a.post_volume(days=30)        # {date: count} per-day post volume
    a.post_success_rate(days=30)  # {total, success, failed, rate}
    a.quota_usage()               # today's usage per action
    a.top_posting_hours(days=90)  # {hour(0-23): count}
    a.top_posting_days(days=90)   # {weekday: count}
    a.recent_posts(limit=10)      # list of recent post audit rows
    a.summary(days=30)            # rolled-up dict

Conventions
-----------
* "post" = ``audit_log.action == 'post'`` only (excludes comments, reactions,
  connections, messages). The CLI/MCP tools that surface analytics are aimed
  at operators who want to know *how their posting cadence is going*.
* Dates are UTC. The DB stores ``created_at`` as ISO-8601 with seconds;
  we slice on the leading 10 chars (``YYYY-MM-DD``).
* Hour-of-day is taken from the *local UTC* hour of the audit row.
* Weekday name is English (``Monday``..``Sunday``) — stable, parseable.

The class is intentionally cheap: it constructs a fresh per-call cursor
through the existing thread-safe ``DB`` object. No caching, no background
threads.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import DB


# Same as DB.audit()'s valid values, but spelled out for self-documentation.
_KNOWN_STATUSES = ("success", "failed", "dry_run", "blocked_safety", "rate_limited")
_WEEKDAY_NAMES = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


class Analytics:
    """Read-only analytics over the audit_log + daily_quotas tables.

    The DB is injected (not constructed here) so tests can use a
    ``tmp_path`` DB. Nothing here ever writes — failures of the read
    path are surfaced as exceptions.
    """

    def __init__(self, db: DB):
        self.db = db

    # -- helpers --------------------------------------------------------

    @staticmethod
    def _now_utc() -> datetime:
        return datetime.now(timezone.utc)

    def _since(self, days: int) -> str:
        """ISO timestamp ``days`` ago (UTC, seconds precision)."""
        if days < 1:
            days = 1  # never query "since now"
        cutoff = self._now_utc() - timedelta(days=days)
        return cutoff.isoformat(timespec="seconds")

    def _since_day(self, days: int) -> str:
        """YYYY-MM-DD string for the start of the window (UTC)."""
        if days < 1:
            days = 1
        cutoff = self._now_utc() - timedelta(days=days)
        return cutoff.strftime("%Y-%m-%d")

    def _conn(self) -> sqlite3.Connection:
        """Direct connection handle — uses DB's lock for thread safety.

        We read directly off the underlying connection rather than going
        through ``DB.get_audit`` because the analytics queries need
        ``GROUP BY``/``strftime`` aggregations that the public API
        doesn't expose.
        """
        return self.db._conn  # type: ignore[attr-defined]

    # -- 1. post_volume --------------------------------------------------

    def post_volume(self, days: int = 30) -> dict[str, int]:
        """Return a per-day count of post actions in the window.

        Output keys are ``YYYY-MM-DD`` strings in UTC. Days with zero
        posts are *included* with value 0 so the time series is gap-free
        (downstream chart code can rely on a dense series). The series
        is sorted oldest -> newest.
        """
        if days < 1:
            days = 1
        since = self._since(days)
        with self.db._lock:  # type: ignore[attr-defined]
            rows = self._conn().execute(
                "SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS c "
                "FROM audit_log "
                "WHERE action = 'post' AND created_at >= ? "
                "GROUP BY day ORDER BY day ASC",
                (since,),
            ).fetchall()

        counts: dict[str, int] = {r["day"]: r["c"] for r in rows}
        # Fill in zero-count days so the series is dense.
        start = self._now_utc().date() - timedelta(days=days - 1)
        dense: dict[str, int] = {}
        for i in range(days):
            d = (start + timedelta(days=i)).isoformat()
            dense[d] = counts.get(d, 0)
        return dense

    # -- 2. post_success_rate -------------------------------------------

    def post_success_rate(self, days: int = 30) -> dict[str, Any]:
        """Roll-up of post outcomes in the window.

        Counts both ``success`` and ``failed`` as attempts; ``dry_run``,
        ``blocked_safety`` and ``rate_limited`` are tracked but **not**
        counted as failures (they never reached LinkedIn). Returned shape::

            {
              "total":     <int>,    # total post rows in window
              "success":   <int>,    # status == 'success'
              "failed":    <int>,    # status == 'failed'
              "dry_run":   <int>,    # status == 'dry_run'
              "blocked":   <int>,    # status == 'blocked_safety' | 'rate_limited'
              "rate":      <float>,  # success / (success+failed); 0.0 if no attempts
              "days":      <int>,
            }
        """
        if days < 1:
            days = 1
        since = self._since(days)
        with self.db._lock:  # type: ignore[attr-defined]
            rows = self._conn().execute(
                "SELECT status, COUNT(*) AS c "
                "FROM audit_log "
                "WHERE action = 'post' AND created_at >= ? "
                "GROUP BY status",
                (since,),
            ).fetchall()

        by_status: dict[str, int] = {r["status"]: r["c"] for r in rows}
        success = by_status.get("success", 0)
        failed = by_status.get("failed", 0)
        dry_run = by_status.get("dry_run", 0)
        blocked = by_status.get("blocked_safety", 0) + by_status.get(
            "rate_limited", 0
        )
        total = sum(by_status.values())
        attempts = success + failed
        rate = (success / attempts) if attempts else 0.0
        return {
            "total": total,
            "success": success,
            "failed": failed,
            "dry_run": dry_run,
            "blocked": blocked,
            "rate": rate,
            "days": days,
        }

    # -- 3. quota_usage --------------------------------------------------

    def quota_usage(self) -> dict[str, Any]:
        """Today's per-action-type quota usage, read from daily_quotas.

        Each action type is reported with ``used`` and ``day``; we don't
        know the user's configured cap here (that's config, not DB), so
        we surface the raw count. The CLI / MCP layers can join in
        the cap from ``Config.safety`` when they need it.
        """
        today = self._now_utc().strftime("%Y-%m-%d")
        with self.db._lock:  # type: ignore[attr-defined]
            rows = self._conn().execute(
                "SELECT action, count, last_action_at "
                "FROM daily_quotas WHERE day = ? ORDER BY action ASC",
                (today,),
            ).fetchall()
        actions = [
            {
                "action": r["action"],
                "used": r["count"],
                "last_action_at": r["last_action_at"],
            }
            for r in rows
        ]
        return {
            "day": today,
            "actions": actions,
            "total": sum(a["used"] for a in actions),
        }

    # -- 4. top_posting_hours -------------------------------------------

    def top_posting_hours(self, days: int = 90) -> dict[int, int]:
        """Distribution of post audits by hour-of-day (0..23, UTC).

        Every hour is included (zero-filled) so a CLI table renders a
        full 24-row grid even when most hours are empty.
        """
        if days < 1:
            days = 1
        since = self._since(days)
        with self.db._lock:  # type: ignore[attr-defined]
            rows = self._conn().execute(
                "SELECT CAST(strftime('%H', created_at) AS INTEGER) AS hr, "
                "       COUNT(*) AS c "
                "FROM audit_log "
                "WHERE action = 'post' AND created_at >= ? "
                "GROUP BY hr ORDER BY hr ASC",
                (since,),
            ).fetchall()

        out: dict[int, int] = {h: 0 for h in range(24)}
        for r in rows:
            out[r["hr"]] = r["c"]
        return out

    # -- 5. top_posting_days --------------------------------------------

    def top_posting_days(self, days: int = 90) -> dict[str, int]:
        """Distribution of post audits by weekday name (Monday..Sunday).

        All seven days are present in the output (zero-filled) for a
        stable ordering. Names are English and stable.
        """
        if days < 1:
            days = 1
        since = self._since(days)
        with self.db._lock:  # type: ignore[attr-defined]
            rows = self._conn().execute(
                "SELECT strftime('%w', created_at) AS dow, COUNT(*) AS c "
                "FROM audit_log "
                "WHERE action = 'post' AND created_at >= ? "
                "GROUP BY dow ORDER BY dow ASC",
                (since,),
            ).fetchall()

        out: dict[str, int] = {name: 0 for name in _WEEKDAY_NAMES}
        # strftime %w: 0=Sunday, 1=Monday, ..., 6=Saturday (UTC).
        # SQLite strftime returns strings, so coerce via int().
        dow_to_name = {
            0: "Sunday",
            1: "Monday",
            2: "Tuesday",
            3: "Wednesday",
            4: "Thursday",
            5: "Friday",
            6: "Saturday",
        }
        for r in rows:
            try:
                key = int(r["dow"])
            except (TypeError, ValueError):
                continue
            name = dow_to_name.get(key)
            if name is not None:
                out[name] = r["c"]
        return out

    # -- 6. recent_posts -------------------------------------------------

    def recent_posts(self, limit: int = 10) -> list[dict[str, Any]]:
        """Most recent ``post`` audit rows, newest first.

        Each entry is a plain dict with the audit columns needed for a
        human-readable listing. ``dry_run`` is converted back to bool
        and ``detail`` is left as raw JSON (CLI / MCP layers can
        pretty-print or ignore).
        """
        if limit < 1:
            limit = 1
        with self.db._lock:  # type: ignore[attr-defined]
            rows = self._conn().execute(
                "SELECT id, action, target, status, dry_run, detail, created_at "
                "FROM audit_log WHERE action = 'post' "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "action": r["action"],
                "target": r["target"],
                "status": r["status"],
                "dry_run": bool(r["dry_run"]),
                "detail": r["detail"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    # -- 7. summary ------------------------------------------------------

    def summary(self, days: int = 30) -> dict[str, Any]:
        """One-call roll-up of the most useful metrics.

        Combines ``post_success_rate`` + ``quota_usage`` + a few derived
        numbers (top hour, top weekday). Designed for the CLI default
        command and the ``get_analytics_summary`` MCP tool.
        """
        rate = self.post_success_rate(days=days)
        quota = self.quota_usage()
        hours = self.top_posting_hours(days=max(days, 90))
        # Use the full 90-day hour/day distributions to give stable
        # "best time to post" hints even when the recent window is small.
        weekdays = self.top_posting_days(days=max(days, 90))

        # Find the single most-active hour (or None if everything is 0).
        total_posts = sum(hours.values())
        best_hour: int | None = None
        if total_posts:
            best_hour = max(hours.items(), key=lambda kv: kv[1])[0]

        best_day: str | None = None
        if sum(weekdays.values()):
            best_day = max(weekdays.items(), key=lambda kv: kv[1])[0]

        return {
            "days": days,
            "success_rate": rate,
            "quota": quota,
            "top_hour": best_hour,
            "top_hour_count": hours.get(best_hour, 0) if best_hour is not None else 0,
            "top_day": best_day,
            "top_day_count": weekdays.get(best_day, 0) if best_day is not None else 0,
            "total_posts_in_window": rate["total"],
        }


__all__ = ["Analytics"]
