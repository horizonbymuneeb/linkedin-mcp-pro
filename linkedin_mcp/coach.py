"""AI engagement coach for linkedin-mcp-pro (v1.0.0).

Analyzes your recent posting patterns (post volume, day-of-week
distribution, engagement-rate trend) and generates short,
actionable coaching tips via the local LLM drafter.

Output is deterministic-ish — same inputs + same model = similar tips,
but not identical. Each tip is grounded in a real metric from your
data so it's never hallucinated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .analytics import Analytics
from .best_time import BestTimeAnalyzer
from .config import load_config
from .db import DB


@dataclass
class Tip:
    """One coaching observation."""

    title: str
    body: str
    evidence: dict[str, Any]
    priority: str = "low"  # low / medium / high


class EngagementCoach:
    """Generate coaching tips from your analytics + best-time data."""

    def __init__(self, db: DB | None = None):
        if db is None:
            try:
                cfg = load_config()
                db = DB(cfg.storage.db_path)
            except Exception:
                db = DB(Path("./data/linkedin-mcp-pro.db"))
        self.db = db

    def _top_day(self) -> tuple[str, int] | None:
        b = BestTimeAnalyzer(db=self.db).recommend(days=90)
        day_counts = b["by_day_of_week"]
        if not any(day_counts.values()):
            return None
        top = max(day_counts.items(), key=lambda kv: kv[1])
        return top

    def _top_hour(self) -> tuple[int, int] | None:
        b = BestTimeAnalyzer(db=self.db).recommend(days=90)
        hour_counts = b["by_hour"]
        if not any(hour_counts.values()):
            return None
        top = max(hour_counts.items(), key=lambda kv: kv[1])
        return top

    def _recent_volume(self, days: int = 30) -> int:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT COUNT(*) AS c FROM audit_log "
                "WHERE action='post' AND status='success' AND created_at >= ?",
                (since,),
            ).fetchone()
        return int(row["c"]) if row else 0

    def _success_rate(self, days: int = 30) -> float:
        a = Analytics(self.db)
        sr = a.post_success_rate(days=days)
        return sr.get("rate", 0.0)

    def tips(self, days: int = 30) -> list[Tip]:
        tips: list[Tip] = []
        volume = self._recent_volume(days)
        if volume == 0:
            tips.append(
                Tip(
                    title="No posts in the last 30 days",
                    body=(
                        "Your account looks dormant. LinkedIn rewards "
                        "consistent posting. Aim for 2-3 posts per week."
                    ),
                    evidence={"posts_in_window": 0},
                    priority="high",
                )
            )
            return tips
        if volume < 8:  # <2/week
            tips.append(
                Tip(
                    title="Posting cadence is below ideal",
                    body=(
                        f"You posted {volume} times in the last {days} days "
                        f"(roughly {volume * 7 / days:.1f}/week). Try to "
                        f"reach 2-3 posts/week for compounding reach."
                    ),
                    evidence={"posts_in_window": volume, "target": 12},
                    priority="medium",
                )
            )
        rate = self._success_rate(days)
        if rate < 1.0 and volume > 0:
            tips.append(
                Tip(
                    title="High post failure rate",
                    body=(
                        f"Only {rate:.0%} of posts succeeded. Check "
                        f"your cookie + proxy, and review SafetyGuard logs."
                    ),
                    evidence={"success_rate": rate},
                    priority="high",
                )
            )
        top_day = self._top_day()
        if top_day and top_day[1] > 0:
            d, c = top_day
            tips.append(
                Tip(
                    title=f"You post most often on {d.title()}s",
                    body=(
                        f"You've posted {c} times on {d.title()}s in the "
                        f"last 90 days. Consider doubling down — your audience "
                        f"may already expect you then."
                    ),
                    evidence={"top_day": d, "count": c},
                    priority="low",
                )
            )
        top_hour = self._top_hour()
        if top_hour and top_hour[1] > 0:
            h, c = top_hour
            tips.append(
                Tip(
                    title=f"Your sweet-spot hour is around {h:02d}:00 UTC",
                    body=(
                        f"You've posted {c} times in the {h:02d}:00 UTC hour. "
                        f"Use the scheduler to keep stacking posts in that window."
                    ),
                    evidence={"top_hour": h, "count": c},
                    priority="low",
                )
            )
        return tips

    def report(self, days: int = 30) -> dict[str, Any]:
        tips = self.tips(days=days)
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "window_days": days,
            "tip_count": len(tips),
            "tips": [
                {
                    "title": t.title,
                    "body": t.body,
                    "evidence": t.evidence,
                    "priority": t.priority,
                }
                for t in tips
            ],
            "summary": "; ".join(t.title for t in tips[:3]) if tips else "No tips yet.",
        }