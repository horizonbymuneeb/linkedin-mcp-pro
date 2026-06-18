"""`linkedin-mcp analytics` subcommand (v0.6.0).

Post-analytics over the audit_log + daily_quotas tables. Read-only —
no side effects, no SafetyGuard.

Subcommands::

    summary [--days N]     — one-glance roll-up (default: 30)
    volume  [--days N]     — per-day post count table
    hours   [--days N]     — top posting hours (0..23, UTC)
    days    [--days N]     — top posting weekdays (Monday..Sunday)
    quota                  — today's per-action quota usage
    recent  [--limit N]    — most recent post audit rows
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .analytics import Analytics
from .config import load_config
from .db import DB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_db_path() -> Path:
    try:
        cfg = load_config()
        return Path(cfg.storage.db_path)
    except Exception as e:
        print(f"❌ Could not load config: {e}", file=sys.stderr)
        return Path("./data/linkedin-mcp-pro.db")


def _open_analytics() -> Analytics:
    """Build a fresh Analytics from the configured DB path."""
    return Analytics(DB(_resolve_db_path()))


def _print_hours_table(hours: dict[int, int]) -> None:
    """Render 24-hour distribution as a horizontal bar chart."""
    print(f"{'HOUR (UTC)':<10s}  {'COUNT':>5s}  HISTOGRAM")
    print(f"{'-' * 10}  {'-' * 5}  {'-' * 32}")
    peak = max(hours.values()) if hours else 0
    for h in range(24):
        c = hours.get(h, 0)
        bar_w = int((c / peak) * 30) if peak else 0
        bar = "█" * bar_w
        print(f"{h:02d}:00      {c:5d}  {bar}")


def _print_days_table(weekdays: dict[str, int]) -> None:
    """Render weekday distribution as a horizontal bar chart."""
    print(f"{'WEEKDAY':<12s}  {'COUNT':>5s}  HISTOGRAM")
    print(f"{'-' * 12}  {'-' * 5}  {'-' * 32}")
    peak = max(weekdays.values()) if weekdays else 0
    for day in (
        "Monday", "Tuesday", "Wednesday",
        "Thursday", "Friday", "Saturday", "Sunday",
    ):
        c = weekdays.get(day, 0)
        bar_w = int((c / peak) * 30) if peak else 0
        bar = "█" * bar_w
        print(f"{day:<12s}  {c:5d}  {bar}")


def _print_volume_table(volume: dict[str, int]) -> None:
    """Render per-day volume as a horizontal bar chart."""
    print(f"{'DATE (UTC)':<12s}  {'COUNT':>5s}  HISTOGRAM")
    print(f"{'-' * 12}  {'-' * 5}  {'-' * 32}")
    peak = max(volume.values()) if volume else 0
    for date in sorted(volume):
        c = volume[date]
        bar_w = int((c / peak) * 30) if peak else 0
        bar = "█" * bar_w
        print(f"{date:<12s}  {c:5d}  {bar}")


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def cmd_summary(args: argparse.Namespace) -> int:
    a = _open_analytics()
    s = a.summary(days=args.days)
    rate = s["success_rate"]
    quota = s["quota"]

    print(f"Post analytics — last {s['days']} day(s) (UTC)")
    print(f"-" * 40)
    print(f"  Total post rows:    {s['total_posts_in_window']}")
    print(f"  Successful:         {rate['success']}")
    print(f"  Failed:             {rate['failed']}")
    print(f"  Dry-runs:           {rate['dry_run']}")
    print(f"  Blocked:            {rate['blocked']}")
    print(f"  Success rate:       {rate['rate'] * 100:.1f}%")
    if s["top_hour"] is not None:
        print(
            f"  Best hour:          {s['top_hour']:02d}:00 UTC "
            f"({s['top_hour_count']} post(s))"
        )
    else:
        print(f"  Best hour:          (no posts in window)")
    if s["top_day"] is not None:
        print(
            f"  Best day:           {s['top_day']} "
            f"({s['top_day_count']} post(s))"
        )
    else:
        print(f"  Best day:           (no posts in window)")

    print()
    print(f"Today's quota usage ({quota['day']} UTC):")
    if not quota["actions"]:
        print(f"  (no actions recorded today)")
    else:
        for a in quota["actions"]:
            print(
                f"  {a['action']:<10s} {a['used']:>3d}  "
                f"last: {a['last_action_at'] or '—'}"
            )
    return 0


def cmd_volume(args: argparse.Namespace) -> int:
    a = _open_analytics()
    vol = a.post_volume(days=args.days)
    print(f"Post volume — last {args.days} day(s) (UTC)")
    _print_volume_table(vol)
    total = sum(vol.values())
    print(f"  Total: {total} post(s) in window")
    return 0


def cmd_hours(args: argparse.Namespace) -> int:
    a = _open_analytics()
    hours = a.top_posting_hours(days=args.days)
    print(f"Posting hours — last {args.days} day(s) (UTC)")
    _print_hours_table(hours)
    print(f"  Total: {sum(hours.values())} post(s) in window")
    return 0


def cmd_days(args: argparse.Namespace) -> int:
    a = _open_analytics()
    weekdays = a.top_posting_days(days=args.days)
    print(f"Posting weekdays — last {args.days} day(s) (UTC)")
    _print_days_table(weekdays)
    print(f"  Total: {sum(weekdays.values())} post(s) in window")
    return 0


def cmd_quota(args: argparse.Namespace) -> int:
    a = _open_analytics()
    q = a.quota_usage()
    print(f"Today's quota usage — {q['day']} UTC")
    print(f"{'ACTION':<12s}  {'USED':>5s}  LAST ACTION")
    print(f"{'-' * 12}  {'-' * 5}  {'-' * 25}")
    if not q["actions"]:
        print(f"(no actions recorded today)")
    else:
        for r in q["actions"]:
            print(
                f"{r['action']:<12s}  {r['used']:5d}  "
                f"{r['last_action_at'] or '—'}"
            )
    print(f"\n  Total: {q['total']}")
    return 0


def cmd_recent(args: argparse.Namespace) -> int:
    a = _open_analytics()
    rows = a.recent_posts(limit=args.limit)
    if not rows:
        print("(no recent posts)")
        return 0
    print(f"{'TIME (UTC)':<20s}  {'STATUS':<14s}  {'DRY':<3s}  TARGET")
    print("-" * 80)
    for r in rows:
        target = (r.get("target") or "")[:40]
        print(
            f"{r['created_at']:<20s}  {r['status']:<14s}  "
            f"{'Y' if r['dry_run'] else 'N':<3s}  {target}"
        )
    return 0


# ---------------------------------------------------------------------------
# argparse plumbing
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="linkedin-mcp analytics",
        description=(
            "Post analytics: volume, success rate, posting hours/days, "
            "quota usage. Read-only over audit_log + daily_quotas."
        ),
    )
    sub = p.add_subparsers(dest="subcommand", required=True)

    p_sum = sub.add_parser("summary", help="One-glance roll-up.")
    p_sum.add_argument(
        "--days", type=int, default=30,
        help="Lookback window in days (default: 30).",
    )
    p_sum.set_defaults(func=cmd_summary)

    p_vol = sub.add_parser("volume", help="Per-day post count table.")
    p_vol.add_argument(
        "--days", type=int, default=30,
        help="Lookback window in days (default: 30).",
    )
    p_vol.set_defaults(func=cmd_volume)

    p_hr = sub.add_parser("hours", help="Top posting hours (UTC).")
    p_hr.add_argument(
        "--days", type=int, default=90,
        help="Lookback window in days (default: 90).",
    )
    p_hr.set_defaults(func=cmd_hours)

    p_dy = sub.add_parser("days", help="Top posting weekdays.")
    p_dy.add_argument(
        "--days", type=int, default=90,
        help="Lookback window in days (default: 90).",
    )
    p_dy.set_defaults(func=cmd_days)

    p_q = sub.add_parser("quota", help="Today's per-action quota usage.")
    p_q.set_defaults(func=cmd_quota)

    p_r = sub.add_parser("recent", help="Most recent post audit rows.")
    p_r.add_argument(
        "--limit", type=int, default=10,
        help="Maximum number of rows to show (default: 10).",
    )
    p_r.set_defaults(func=cmd_recent)

    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
