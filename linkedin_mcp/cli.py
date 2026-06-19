"""linkedin-mcp-pro CLI utilities.

Commands:
  linkedin-mcp-health   — check server status, daily usage
  linkedin-mcp-stats    — print audit log
  linkedin-mcp-login    — open browser, log in once, save persistent profile (v0.3+)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from .browser import interactive_login
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
        print(f"⚠️  Config issues:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        # Don't exit early — still show DB + quotas (informational)

    print(f"✓ Config OK")
    print(f"  Server: {cfg.server.host}:{cfg.server.port} ({cfg.server.transport})")
    print(f"  Daily limits: {cfg.safety.daily_limit_connection_requests} conn, "
          f"{cfg.safety.daily_limit_posts} posts, {cfg.safety.daily_limit_messages} msg, "
          f"{cfg.safety.daily_limit_comments} comments")
    print(f"  Business hours: {cfg.safety.business_hours_start:02d}:00-"
          f"{cfg.safety.business_hours_end:02d}:00 UTC ({', '.join(cfg.safety.business_days)})")
    print(f"  Warmup: {'on' if cfg.safety.warmup_enabled else 'off'}")
    print(f"  Jitter: {cfg.safety.action_jitter_min_seconds}-{cfg.safety.action_jitter_max_seconds}s")
    print(f"  Browser profile: {cfg.storage.browser_profile_dir}")
    if cfg.storage.browser_profile_dir.exists():
        print(f"    [OK] profile exists")
    else:
        print(f"    [MISSING] run `linkedin-mcp login` to create it")

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


async def login_async(timeout_seconds: int = 300, headless: bool = False) -> int:
    """Open browser, user logs in, profile is saved.

    Args:
      timeout_seconds: max wait for user to complete login.
      headless: if True, run browser without visible window. Useful for
                CI or when called from web UI; in headless mode you must
                provide credentials another way (e.g. cookie paste).

    Returns 0 on success, 1 on failure.
    """
    cfg = load_config()
    profile = Path(cfg.storage.browser_profile_dir)

    if profile.exists():
        from .browser import has_valid_session
        if has_valid_session(profile):
            print(f"Existing session found at {profile}.", file=sys.stderr)
            ans = input("Overwrite with new login? [y/N] ").strip().lower()
            if ans not in ("y", "yes"):
                print("Aborted. Existing session preserved.", file=sys.stderr)
                return 0

    print(f"Opening browser to LinkedIn login (headless={headless})...", file=sys.stderr)
    print(f"Profile will be saved at: {profile}", file=sys.stderr)
    print(f"", file=sys.stderr)

    try:
        success = await interactive_login(cfg, headless=headless, timeout=timeout_seconds)
    except KeyboardInterrupt:
        print(f"\nLogin cancelled by user.", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"❌ Login failed: {e}", file=sys.stderr)
        return 1

    if success:
        print(f"", file=sys.stderr)
        print(f"✓ Login complete. Profile saved at: {profile}", file=sys.stderr)
        print(f"  All future linkedin-mcp calls will use this session.", file=sys.stderr)
        print(f"  Run `linkedin-mcp serve` to start the MCP server.", file=sys.stderr)
        return 0
    else:
        print(f"❌ Login did not complete. URL did not reach /feed/.", file=sys.stderr)
        return 1


def login() -> int:
    """Sync wrapper for the login command."""
    import argparse
    ap = argparse.ArgumentParser(prog="linkedin-mcp-login", description="Log in to LinkedIn once, save persistent profile.")
    ap.add_argument("--headless", action="store_true", help="Run browser without visible window")
    ap.add_argument("--headed", dest="headless", action="store_false", help="Run browser with visible window (default)")
    ap.set_defaults(headless=False)
    ap.add_argument("--timeout", type=int, default=300, help="Max seconds to wait for login (default 300)")
    args = ap.parse_args()
    return asyncio.run(login_async(timeout_seconds=args.timeout, headless=args.headless))


if __name__ == "__main__":
    sys.exit(health())
