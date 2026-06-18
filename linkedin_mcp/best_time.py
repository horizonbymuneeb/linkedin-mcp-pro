"""Best-time analyzer for linkedin-mcp-pro (v0.6.0).

Looks at your posting history (audit_log) and recommends the slots
(hour-of-day × day-of-week) where you've posted the most. We don't
have per-post engagement data here — the v0.6.0 analyzer is volume-
based. A future version (with reaction counts) will switch to
engagement-based scoring.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import load_config
from .db import DB


class BestTimeAnalyzer:
    """Recommend the best posting slots from your own history.

    Slots are bucketed by (day_of_week, hour). The default lookback
    is 90 days. With no data, returns a small static set of safe
    defaults so callers don't need to special-case empty state.
    """

    DEFAULT_DAYS = 90
    DEFAULT_TOP_N = 5
    SAFE_DEFAULTS: list[tuple[str, int]] = [
        ("tue", 9), ("wed", 10), ("thu", 14), ("mon", 11), ("fri", 8),
    ]

    def __init__(self, db: DB | None = None):
        if db is None:
            try:
                cfg = load_config()
                db = DB(cfg.storage.db_path)
            except Exception:
                db = DB(Path("./data/linkedin-mcp-pro.db"))
        self.db = db

    def _since(self, days: int) -> str:
        return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
            timespec="seconds"
        )

    def _query(self, days: int) -> list[str]:
        """Return list of ISO timestamps of all 'post' actions with success status."""
        since = self._since(days)
        with self.db._lock:
            rows = self.db._conn.execute(
                "SELECT created_at FROM audit_log "
                "WHERE action='post' AND status='success' AND created_at >= ? "
                "ORDER BY created_at",
                (since,),
            ).fetchall()
        return [r["created_at"] for r in rows]

    @staticmethod
    def _parse(ts: str) -> datetime:
        # SQLite returns ISO strings, accept both with/without tz
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def recommend(self, days: int = DEFAULT_DAYS, top_n: int = DEFAULT_TOP_N) -> dict[str, Any]:
        timestamps = self._query(days)
        if not timestamps:
            return {
                "data_points": 0,
                "by_hour": {h: 0 for h in range(24)},
                "by_day_of_week": {d: 0 for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")},
                "best_slots": [
                    {"day": d, "hour": h, "post_count": 0}
                    for d, h in self.SAFE_DEFAULTS[:top_n]
                ],
                "note": "No data yet — returning safe defaults (Tue 9, Wed 10, Thu 14 UTC).",
            }
        by_hour: Counter[int] = Counter()
        by_day: Counter[str] = Counter()
        slots: Counter[tuple[str, int]] = Counter()
        day_names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        for ts in timestamps:
            dt = self._parse(ts)
            by_hour[dt.hour] += 1
            d = day_names[dt.weekday()]
            by_day[d] += 1
            slots[(d, dt.hour)] += 1
        # Zero-fill so the response is always dense
        by_hour_d = {h: by_hour.get(h, 0) for h in range(24)}
        by_day_d = {d: by_day.get(d, 0) for d in day_names}
        best = slots.most_common(top_n)
        return {
            "data_points": len(timestamps),
            "by_hour": by_hour_d,
            "by_day_of_week": by_day_d,
            "best_slots": [
                {"day": d, "hour": h, "post_count": c}
                for (d, h), c in best
            ],
            "note": "Volume-based ranking (no engagement data). Times are UTC.",
        }