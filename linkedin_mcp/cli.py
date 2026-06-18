"""linkedin-mcp-pro CLI utilities.

`linkedin-mcp-health` — check server status
`linkedin-mcp-stats`  — print daily stats
"""

from __future__ import annotations

import sys
from pathlib import Path

from .config import load_config
from .db import DB


def health() -> int:
    """Check DB, config, and (if reachable) cookie validity. Exit 0 if all OK."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f"❌ Config load failed: {e}", file=sys.stderr)
        return 1

    errors = cfg.validate()
    if errors:
        print("⚠️  Config issues:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(f"✓ Config OK")
    print(f"  Server: {cfg.server.host}:{cfg.server.port} ({cfg.server.transport})")
    print(f"  Daily limits: {cfg.safety.daily_limit_connection_requests} conn, "
          f"{cfg.safety.daily_limit_posts} posts, {cfg.safety.daily_limit_messages} msg, "
          f"{cfg.safety.daily_limit_comments} comments")
    print(f"  Business hours: {cfg.safety.business_hours_start:02d}:00-"
          f"{cfg.safety.business_hours_end:02d}:00 UTC ({', '.join(cfg.safety.business_days)})")
    print(f"  Warmup: {'on' if cfg.safety.warmup_enabled else 'off'}")
    print(f"  Jitter: {cfg.safety.action_jitter_min_seconds}-{cfg.safety.action_jitter_max_seconds}s")

    try:
        db = DB(cfg.storage.db_path)
        print(f"✓ DB OK ({cfg.storage.db_path})")
    except Exception as e:
        print(f"❌ DB error: {e}", file=sys.stderr)
        return 1

    # Print quotas
    limits = {
        "connection": cfg.safety.daily_limit_connection_requests,
        "post": cfg.safety.daily_limit_posts,
        "message": cfg.safety.daily_limit_messages,
        "comment": cfg.safety.daily_limit_comments,
        "reaction": cfg.safety.daily_limit_reactions,
    }
    print(f"\n  Today's usage:")
    for q in db.get_all_quotas(limits):
        bar = "█" * int(q.percent / 10) + "░" * (10 - int(q.percent / 10))
        print(f"    {q.action:10s} {bar} {q.used:3d}/{q.limit:3d}  zone={q.zone}")

    db.close()
    return 0


def stats(action: str | None = None, limit: int = 20) -> int:
    """Print recent audit log entries."""
    cfg = load_config()
    db = DB(cfg.storage.db_path)
    rows = db.get_audit(action=action, limit=limit)
    if not rows:
        print("(no audit entries)")
        db.close()
        return 0

    print(f"{'TIME (UTC)':<20s}  {'ACTION':<12s}  {'STATUS':<14s}  {'DRY':<3s}  TARGET")
    print("-" * 90)
    for r in rows:
        target = (r.get("target") or "")[:50]
        print(f"{r['created_at']:<20s}  {r['action']:<12s}  {r['status']:<14s}  "
              f"{'Y' if r['dry_run'] else 'N':<3s}  {target}")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(health())
