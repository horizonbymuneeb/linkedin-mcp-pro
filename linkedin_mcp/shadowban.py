"""Shadow-ban detector for linkedin-mcp-pro (v0.7.0).

Monitors post impressions + engagement over time. If a recent post
shows dramatically lower engagement than your baseline (median of the
prior N posts), raise an alert so you can investigate before the
account gets fully flagged.

We can't directly detect LinkedIn's shadow-ban — we infer it via
sudden engagement drops. This module is read-only over the audit_log
+ any impression data you've recorded (via analytics).

Detection signals:
  1. Impressions drop >50% vs trailing median (configurable)
  2. Zero engagement on a post that historically got engagement
  3. Engagement rate drops to near-zero for 3+ consecutive posts
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from .config import load_config
from .db import DB


class ShadowBanError(Exception):
    """Raised for any shadowban-detector failure."""


@dataclass
class PostSignal:
    """One post's recorded impressions + engagement."""

    created_at: str
    target: str  # post URL or id
    impressions: int
    engagement: int

    @property
    def rate(self) -> float:
        return (self.engagement / self.impressions) if self.impressions > 0 else 0.0


class ShadowBanDetector:
    """Monitor posting engagement and detect suspicious drops.

    Inputs come from two sources:
      - ``audit_log`` rows where ``action='post'`` (counts successful posts)
      - ``session_state`` keys ``post_impressions:<target>`` /
        ``post_engagement:<target>`` (set by analytics tools that scrape
        LinkedIn for you, or by the optional impression-recorder)

    For now we work with whatever is in session_state (your recording
    pipeline). If nothing is recorded yet, we report 'no_data'.
    """

    DEFAULT_DROP_THRESHOLD = 0.50  # 50% drop = suspicious
    DEFAULT_MIN_BASELINE_POSTS = 5

    def __init__(self, db: DB | None = None):
        if db is None:
            try:
                cfg = load_config()
                db = DB(cfg.storage.db_path)
            except Exception:
                db = DB(Path("./data/linkedin-mcp-pro.db"))
        self.db = db

    def record_post_metrics(
        self,
        target: str,
        impressions: int,
        engagement: int,
        when: datetime | None = None,
    ) -> None:
        """Record metrics for a post (called by your scraper / analytics)."""
        when = when or datetime.now(timezone.utc)
        ts = when.isoformat(timespec="seconds")
        self.db.set_state(f"post_impressions:{target}", str(impressions))
        self.db.set_state(f"post_engagement:{target}", str(engagement))
        self.db.set_state(f"post_recorded_at:{target}", ts)

    def list_post_signals(self) -> list[PostSignal]:
        """Return every post with recorded metrics, oldest first."""
        with self.db._lock:
            rows = self.db._conn.execute(
                "SELECT key, value FROM session_state "
                "WHERE key LIKE 'post_recorded_at:%' "
                "ORDER BY value"
            ).fetchall()
        signals: list[PostSignal] = []
        for r in rows:
            target = r["key"][len("post_recorded_at:"):]
            ts = r["value"]
            imp = self.db.get_state(f"post_impressions:{target}")
            eng = self.db.get_state(f"post_engagement:{target}")
            if imp is None or eng is None:
                continue
            try:
                signals.append(
                    PostSignal(
                        created_at=ts,
                        target=target,
                        impressions=int(imp),
                        engagement=int(eng),
                    )
                )
            except ValueError:
                continue
        return signals

    @staticmethod
    def _median(values: Iterable[int]) -> float:
        vals = sorted(values)
        n = len(vals)
        if n == 0:
            return 0.0
        if n % 2 == 1:
            return float(vals[n // 2])
        return (vals[n // 2 - 1] + vals[n // 2]) / 2.0

    def check(
        self,
        drop_threshold: float = DEFAULT_DROP_THRESHOLD,
        min_baseline_posts: int = DEFAULT_MIN_BASELINE_POSTS,
    ) -> dict[str, Any]:
        """Run a shadow-ban check.

        Returns:
            {
              "status": "ok" | "warning" | "alert" | "no_data",
              "data_points": int,
              "baseline_engagement_rate": float,
              "latest_engagement_rate": float,
              "drop_pct": float,  # 0.0 - 1.0
              "consecutive_low_posts": int,
              "alerts": [str, ...],
              "checked_at": iso_str,
            }
        """
        signals = self.list_post_signals()
        checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if len(signals) < min_baseline_posts:
            return {
                "status": "no_data",
                "data_points": len(signals),
                "baseline_engagement_rate": 0.0,
                "latest_engagement_rate": 0.0,
                "drop_pct": 0.0,
                "consecutive_low_posts": 0,
                "alerts": [
                    f"Only {len(signals)} posts with metrics recorded; "
                    f"need at least {min_baseline_posts} for a baseline."
                ],
                "checked_at": checked_at,
            }
        baseline = signals[:-3] if len(signals) > 3 else signals[:-1]
        baseline_rates = [s.rate for s in baseline if s.impressions > 0]
        baseline_median = self._median(
            [int(s.rate * 10000) for s in baseline if s.impressions > 0]
        ) / 10000.0
        # Find the latest signal with non-zero impressions (a zero-imp
        # post is data-anomaly, not shadow-ban signal).
        latest = None
        for s in reversed(signals):
            if s.impressions > 0:
                latest = s
                break
        if latest is None:
            return {
                "status": "no_data",
                "data_points": len(signals),
                "baseline_engagement_rate": round(baseline_median, 4),
                "latest_engagement_rate": 0.0,
                "drop_pct": 0.0,
                "consecutive_low_posts": 0,
                "alerts": ["No posts with non-zero impressions yet."],
                "checked_at": checked_at,
            }
        consecutive_low = 0
        for s in reversed(signals):
            if s.impressions > 0 and s.rate < baseline_median * 0.2:
                consecutive_low += 1
            elif s.impressions > 0:
                break
        alerts: list[str] = []
        latest_rate = latest.rate
        drop_pct = 0.0
        if baseline_median > 0:
            drop_pct = max(0.0, (baseline_median - latest_rate) / baseline_median)
        status = "ok"
        if drop_pct >= drop_threshold:
            status = "alert"
            alerts.append(
                f"Latest post engagement rate {latest_rate:.2%} is "
                f"{drop_pct:.0%} below baseline {baseline_median:.2%} "
                f"(threshold {drop_threshold:.0%}). Possible shadow-ban."
            )
        elif consecutive_low >= 3:
            status = "alert"
            alerts.append(
                f"{consecutive_low} consecutive posts had <20% of baseline "
                f"engagement. Pattern suggests suppression."
            )
        elif drop_pct >= drop_threshold / 2:
            status = "warning"
            alerts.append(
                f"Engagement down {drop_pct:.0%} from baseline. Monitor closely."
            )
        return {
            "status": status,
            "data_points": len(signals),
            "baseline_engagement_rate": round(baseline_median, 4),
            "latest_engagement_rate": round(latest_rate, 4),
            "drop_pct": round(drop_pct, 4),
            "consecutive_low_posts": consecutive_low,
            "alerts": alerts,
            "checked_at": checked_at,
            "latest_post": latest.target,
        }