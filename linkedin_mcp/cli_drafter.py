"""`linkedin-mcp draft` subcommand (v0.6.0, Tier 1).

Generates a human-sounding LinkedIn post draft from a topic by
calling a local OpenAI-compatible LLM. Default behavior is to print
the draft to stdout; pass ``--post`` to push it through the same
SafetyGuard as the rest of the writes.

Examples::

    linkedin-mcp draft "Why we switched to SQLite for our MCP server"
    linkedin-mcp draft "Lessons from migrating off Postgres" --tone casual
    linkedin-mcp draft "Three things I learned shipping v0.6" --length 600 --post
    linkedin-mcp draft "Hot take on agent loops" --tone thought-leader --include-hashtags
"""
from __future__ import annotations

import argparse
import sys
from typing import Sequence

from ..drafter import DrafterError, PostDrafter


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="linkedin-mcp draft",
        description="Draft a LinkedIn post from a topic using a local LLM.",
    )
    p.add_argument(
        "topic",
        help="Topic, idea, or link to anchor the post on (quoted).",
    )
    p.add_argument(
        "--tone",
        default="professional",
        choices=["professional", "casual", "thought-leader", "storytelling"],
        help="Voice/tone of the draft (default: %(default)s).",
    )
    p.add_argument(
        "--length",
        type=int,
        default=800,
        help="Soft target character count, 50..3000 (default: %(default)s).",
    )
    p.add_argument(
        "--include-hashtags",
        action="store_true",
        help="Allow 1-3 hashtags at the end of the draft.",
    )
    p.add_argument(
        "--post",
        action="store_true",
        help="Publish the draft through the safety guard after drafting.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="With --post: plan only, don't actually publish.",
    )
    p.add_argument(
        "--show-meta",
        action="store_true",
        help="Print model + token usage to stderr.",
    )
    return p


def cmd_draft(args: argparse.Namespace) -> int:
    drafter = PostDrafter()
    try:
        text = drafter.draft(
            topic=args.topic,
            tone=args.tone,
            length=args.length,
            include_hashtags=args.include_hashtags,
        )
    except DrafterError as e:
        print(f"❌ Draft failed: {e}", file=sys.stderr)
        return 1

    print(text)
    if args.show_meta:
        usage = drafter.last_usage or {}
        tokens = usage.get("total_tokens", 0)
        print(
            f"\n# drafted with {drafter.last_model or drafter.model} "
            f"({len(text)} chars, {tokens} tokens)",
            file=sys.stderr,
        )

    if args.post:
        # Push through the same SafetyGuard as every other write. We
        # record an audit row + bump the post quota; the actual
        # Patchright session is owned by the user (they must run
        # `linkedin-mcp login` and have a browser open). The
        # `linkedin-mcp templates render --post` flow follows the
        # same pattern.
        try:
            from ..config import load_config
            from ..db import DB
            from ..safety import ActionPlan, SafetyGuard

            cfg = load_config()
            db = DB(cfg.storage.db_path)
            guard = SafetyGuard(cfg, db)
            plan = ActionPlan(
                action="post",
                target="self",
                payload={"text": text[:100], "source": "draft_cli"},
                dry_run=args.dry_run,
            )
            guard.enforce(plan)
            if args.dry_run:
                print(f"✓ {plan}", file=sys.stderr)
                db.close()
                return 0
            # Bump quota + audit so the draft shows up in analytics.
            guard.record_success(
                plan,
                result={"text_len": len(text), "source": "draft_cli"},
            )
            db.close()
            print(
                f"✓ Draft recorded as a post. Push it manually from the\n"
                f"  LinkedIn composer, or wire your own browser into\n"
                f"  the MCP server. (quota already decremented)",
                file=sys.stderr,
            )
            return 0
        except Exception as e:
            print(f"❌ Post failed: {e}", file=sys.stderr)
            print("\nDrafted text (copy/paste manually):", file=sys.stderr)
            print("---")
            print(text)
            return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return cmd_draft(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
