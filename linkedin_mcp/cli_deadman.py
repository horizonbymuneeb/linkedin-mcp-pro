"""`linkedin-mcp deadman` subcommand (v0.5.0).

Subcommands::

    status          — show last_post_at, days_since, status, threshold
    check           — force a check, optionally send an alert
    test-alert      — send a test Telegram message (verifies wiring)
    set-threshold N — persist a new threshold to session_state

All four subcommands are read- or config-only — none of them post to
LinkedIn. The only network call is ``test-alert``, which hits
api.telegram.org.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

from .deadman import (
    ALERT_COOLDOWN_HOURS,
    DEFAULT_THRESHOLD_DAYS,
    ENV_BOT_TOKEN,
    ENV_CHAT_ID,
    ENV_THRESHOLD_DAYS,
    DeadManError,
    DeadManSwitch,
)
from .config import load_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_db_path() -> Path:
    """Find the DB path via the same loader the rest of the CLI uses."""
    try:
        cfg = load_config()
        return Path(cfg.storage.db_path)
    except Exception as e:
        print(f"❌ Could not load config: {e}", file=sys.stderr)
        # Fall back to a sane default so the CLI still works in fresh
        # checkouts (e.g. running `linkedin-mcp deadman status` in CI).
        return Path("./data/linkedin-mcp-pro.db")


def _status_emoji(status: str) -> str:
    return {
        "ok": "🟢",
        "warning": "🟡",
        "alert": "🔴",
        "no_posts": "⚪",
    }.get(status, "❓")


def _print_check_row(result: dict) -> None:
    print(f"Dead-man switch status")
    print(f"  Last post:    {result['last_post_at'] or '(never)'}")
    days = result["days_since"]
    days_str = f"{days:.2f}" if isinstance(days, (int, float)) else "n/a"
    print(f"  Days since:   {days_str}")
    print(f"  Threshold:    {result['threshold_days']} days")
    print(
        f"  Status:       {_status_emoji(result['status'])} {result['status']}"
    )
    print(f"  Should alert: {result['should_alert']}")
    if result.get("alert_suppressed_reason"):
        print(f"  Suppressed:   {result['alert_suppressed_reason']}")
    print(f"  Last alert:   {result.get('last_alert_sent_at') or '(never)'}")


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    db_path = _resolve_db_path()
    with DeadManSwitch(db_path) as sw:
        result = sw.check()
    _print_check_row(result)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Force a check, then send an alert if should_alert=True."""
    db_path = _resolve_db_path()
    with DeadManSwitch(db_path) as sw:
        result = sw.check()
    _print_check_row(result)
    print()

    if not result["should_alert"]:
        if result["status"] == "alert":
            print(
                f"⏸  Status is 'alert' but alert was suppressed "
                f"({result.get('alert_suppressed_reason')})."
            )
        else:
            print(f"✓ Status is '{result['status']}' — no alert needed.")
        return 0

    # Should alert → send.
    print("→ Sending Telegram alert...")
    sent = DeadManSwitch(db_path).send_alert(
        days_since=result["days_since"],
        last_post_at=result["last_post_at"],
        force=False,
    )
    if sent:
        print("✓ Alert sent.")
        return 0
    print(
        "⚠️  Alert not sent (Telegram unconfigured or send failed). "
        "Check `linkedin-mcp deadman test-alert`.",
        file=sys.stderr,
    )
    return 1


def cmd_test_alert(args: argparse.Namespace) -> int:
    """Send a test Telegram message to verify the bot token + chat id."""
    token, chat_id = os.environ.get(ENV_BOT_TOKEN), os.environ.get(ENV_CHAT_ID)
    if not token or not chat_id:
        print(
            f"❌ Telegram not configured. Set both:\n"
            f"   {ENV_BOT_TOKEN}=<token>\n"
            f"   {ENV_CHAT_ID}=<chat_id>",
            file=sys.stderr,
        )
        return 2

    db_path = _resolve_db_path()
    sw = DeadManSwitch(db_path)
    try:
        sent = sw.send_alert(force=True, kind="test")
    finally:
        sw.close()
    if sent:
        print("✓ Test alert sent. Check your Telegram chat.")
        return 0
    print("❌ Test alert failed (HTTP error). See server logs.", file=sys.stderr)
    return 1


def cmd_set_threshold(args: argparse.Namespace) -> int:
    db_path = _resolve_db_path()
    sw = DeadManSwitch(db_path)
    try:
        stored = sw.set_threshold(args.days)
    except DeadManError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    finally:
        sw.close()
    print(f"✓ Threshold set to {stored} day(s) (persisted in session_state).")
    print(
        f"  Env {ENV_THRESHOLD_DAYS} will now report {stored} until changed."
    )
    return 0


# ---------------------------------------------------------------------------
# argparse plumbing
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="linkedin-mcp deadman",
        description=(
            "Dead-man switch: alert via Telegram if you stop posting for N days."
        ),
    )
    sub = p.add_subparsers(dest="subcommand", required=True)

    p_status = sub.add_parser(
        "status",
        help="Show current status (last post, days since, threshold).",
    )
    p_status.set_defaults(func=cmd_status)

    p_check = sub.add_parser(
        "check",
        help="Force a check; send an alert if should_alert is true.",
    )
    p_check.set_defaults(func=cmd_check)

    p_test = sub.add_parser(
        "test-alert",
        help="Send a test Telegram message to verify bot wiring.",
    )
    p_test.set_defaults(func=cmd_test_alert)

    p_set = sub.add_parser(
        "set-threshold",
        help="Persist a new threshold (days) to session_state.",
    )
    p_set.add_argument(
        "days",
        type=int,
        help="New threshold in days (>= 1).",
    )
    p_set.set_defaults(func=cmd_set_threshold)

    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
