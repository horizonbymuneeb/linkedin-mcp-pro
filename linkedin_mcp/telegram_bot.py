"""Telegram bot control for linkedin-mcp-pro (v0.6.0).

A long-running Telegram bot that lets you control most of linkedin-mcp-pro
from your phone:

    /start     - welcome message + chat_id (so you can add it to the allowlist)
    /post <text>           - create a LinkedIn post (dry-run by default)
    /template <name> [--var k=v] - render and post a template
    /schedule              - list post schedules (YAML)
    /deadman               - show dead-man status
    /quota                 - show today's quota usage
    /draft <topic>         - draft a post via PostDrafter (preview only)

Security model
--------------
The bot is **locked down by chat_id allowlist**. Every incoming update is
checked against the comma-separated list in
``LINKEDIN_MCP_TELEGRAM_ALLOWED_CHAT_IDS`` before any handler runs. If
the list is empty, the bot silently drops *all* messages (fail-closed).

Network model
-------------
The actual HTTP calls to api.telegram.org use ``urllib`` from the stdlib
(works without python-telegram-bot installed at all) — we use
python-telegram-bot only for the *polling loop* and command routing,
because it gives us free ``/start``, ``/help``, long-polling, retries,
and graceful shutdown.

The two layers (core dispatcher + telegram.ext adapter) are split so the
core logic can be unit-tested without a running event loop.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

log = logging.getLogger("linkedin_mcp.telegram_bot")

# --- env / constants --------------------------------------------------------
ENV_BOT_TOKEN = "LINKEDIN_MCP_TELEGRAM_BOT_TOKEN"
ENV_ALLOWED_CHAT_IDS = "LINKEDIN_MCP_TELEGRAM_ALLOWED_CHAT_IDS"
TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_TIMEOUT = 10


# --- exceptions -------------------------------------------------------------


class TelegramBotError(RuntimeError):
    """Raised for unrecoverable telegram-bot failures (bad token, no chat, etc)."""


# --- allowlist helpers ------------------------------------------------------


def parse_allowed_chat_ids(raw: str | None) -> set[int]:
    """Parse a comma-separated list of chat IDs.

    Whitespace is trimmed, non-numeric entries are silently dropped, and
    duplicates are removed. An empty/whitespace input yields an empty set.
    """
    if not raw:
        return set()
    out: set[int] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.add(int(chunk))
        except ValueError:
            log.warning("telegram_bot: ignoring non-numeric chat_id %r", chunk)
    return out


def is_allowed(chat_id: int, allowed: set[int]) -> bool:
    """Return True iff ``chat_id`` is in the allowlist (or list is empty = drop)."""
    if not allowed:
        return False
    return chat_id in allowed


# --- response dispatcher (the testable core) -------------------------------
#
# The bot is essentially a function: incoming text -> outgoing text.
# We model it as a class with a registry of (command_name, handler) pairs
# so we can dispatch deterministically in unit tests without a fake
# ``Update``/``Context`` pair.


@dataclass
class BotResponse:
    """A reply the bot wants to send."""

    chat_id: int
    text: str
    parse_mode: str = ""  # "" = plain text, "Markdown", "HTML"


@dataclass
class TelegramBot:
    """In-process Telegram bot for linkedin-mcp-pro.

    The ``TelegramBot`` class is the *logic layer*: it knows how to
    parse commands, apply the allowlist, and call into the rest of
    linkedin-mcp-pro (drafter, templates, scheduler, safety guard, etc).

    It does **not** run a polling loop on its own — for that, call
    :func:`run_telegram_bot`, which wires this class up to
    ``telegram.ext.Application``.

    All public methods that take ``chat_id`` short-circuit to an empty
    list (silent drop) if the chat isn't in the allowlist. This is
    fail-closed and matches the production behavior of the bot.
    """

    allowed: set[int] = field(default_factory=set)
    # Optional injection points (tests override these):
    post_fn: Optional[Callable[[str], dict[str, Any]]] = None
    draft_fn: Optional[Callable[[str], str]] = None
    list_schedules_fn: Optional[Callable[[], list[dict[str, Any]]]] = None
    deadman_status_fn: Optional[Callable[[], dict[str, Any]]] = None
    quota_fn: Optional[Callable[[], list[dict[str, Any]]]] = None
    template_render_fn: Optional[
        Callable[[str, dict[str, str]], str]
    ] = None

    # ------------------------------------------------------------------
    # Allowlist
    # ------------------------------------------------------------------
    def check_allowed(self, chat_id: int) -> bool:
        """Return True iff this chat is allowed to issue commands."""
        return is_allowed(chat_id, self.allowed)

    # ------------------------------------------------------------------
    # Command handlers — each returns a *list* of BotResponse (1 usually).
    # They never raise for user errors; they return an error message.
    # ------------------------------------------------------------------
    def cmd_start(self, chat_id: int, _args: list[str]) -> list[BotResponse]:
        return [
            BotResponse(
                chat_id=chat_id,
                text=(
                    "👋 linkedin-mcp-pro bot\n\n"
                    f"Your chat_id: `{chat_id}`\n"
                    "Add it to LINKEDIN_MCP_TELEGRAM_ALLOWED_CHAT_IDS to use the bot.\n\n"
                    "Commands: /post /template /schedule /deadman /quota /draft /help"
                ),
            )
        ]

    def cmd_help(self, chat_id: int, _args: list[str]) -> list[BotResponse]:
        return [
            BotResponse(
                chat_id=chat_id,
                text=(
                    "📚 Commands:\n"
                    "/post <text>              Create a post (dry-run by default)\n"
                    "/template <name> [--var k=v]\n"
                    "                          Render and post a template\n"
                    "/schedule                 List post schedules\n"
                    "/deadman                  Show dead-man status\n"
                    "/quota                    Show today's quota usage\n"
                    "/draft <topic>            Draft a post (preview only)\n"
                    "/start                    Show your chat_id\n"
                ),
            )
        ]

    def cmd_post(self, chat_id: int, args: list[str]) -> list[BotResponse]:
        if not args:
            return [
                BotResponse(chat_id=chat_id, text="usage: /post <text>")
            ]
        text = " ".join(args).strip()
        if not text:
            return [BotResponse(chat_id=chat_id, text="(empty post)")]
        if self.post_fn is None:
            return [
                BotResponse(
                    chat_id=chat_id,
                    text="⚠️  Post function not configured.",
                )
            ]
        result = self.post_fn(text)
        ok = bool(result.get("ok"))
        emoji = "✅" if ok else "❌"
        err = result.get("error") or ""
        return [
            BotResponse(
                chat_id=chat_id,
                text=(
                    f"{emoji} Post {'created' if ok else 'failed'}"
                    f" (chars={len(text)}){(' — ' + err) if err else ''}"
                ),
            )
        ]

    def cmd_template(
        self, chat_id: int, args: list[str]
    ) -> list[BotResponse]:
        if not args:
            return [
                BotResponse(chat_id=chat_id, text="usage: /template <name> [--var k=v]")
            ]
        name = args[0]
        variables: dict[str, str] = {}
        i = 1
        while i < len(args):
            token = args[i]
            if token == "--var":
                # Next token is key=value
                if i + 1 < len(args) and "=" in args[i + 1]:
                    k, v = args[i + 1].split("=", 1)
                    variables[k.strip()] = v
                    i += 2
                    continue
                i += 1
                continue
            if token.startswith("--var="):
                k, v = token[len("--var="):].split("=", 1)
                variables[k.strip()] = v
                i += 1
                continue
            if token.startswith("--var") and "=" in token:
                # --varkey=value
                k, v = token.split("=", 1)
                variables[k.strip()] = v
                i += 1
                continue
            i += 1
        if self.template_render_fn is None:
            return [
                BotResponse(
                    chat_id=chat_id,
                    text="⚠️  Template rendering not configured.",
                )
            ]
        try:
            rendered = self.template_render_fn(name, variables)
        except Exception as e:  # missing template, bad var, etc
            return [
                BotResponse(chat_id=chat_id, text=f"❌ {type(e).__name__}: {e}")
            ]
        # After rendering, push it through the post pipeline (dry-run by default)
        if self.post_fn is None:
            return [
                BotResponse(
                    chat_id=chat_id,
                    text=f"📄 Rendered template:\n\n{rendered}",
                )
            ]
        result = self.post_fn(rendered)
        ok = bool(result.get("ok"))
        emoji = "✅" if ok else "❌"
        return [
            BotResponse(
                chat_id=chat_id,
                text=(
                    f"{emoji} Template '{name}' "
                    f"{'posted' if ok else 'failed'} (chars={len(rendered)})"
                ),
            )
        ]

    def cmd_schedule(self, chat_id: int, _args: list[str]) -> list[BotResponse]:
        if self.list_schedules_fn is None:
            return [
                BotResponse(
                    chat_id=chat_id,
                    text="⚠️  Scheduler not configured.",
                )
            ]
        rows = self.list_schedules_fn()
        if not rows:
            return [BotResponse(chat_id=chat_id, text="(no schedules)")]
        lines = ["📅 Schedules:"]
        for r in rows:
            name = r.get("name", "?")
            when = r.get("next_run") or r.get("cron") or r.get("at") or r.get("time") or "-"
            enabled = "🟢" if r.get("enabled", True) else "⚪"
            lines.append(f"  {enabled} {name}  next={when}")
        return [BotResponse(chat_id=chat_id, text="\n".join(lines))]

    def cmd_deadman(self, chat_id: int, _args: list[str]) -> list[BotResponse]:
        if self.deadman_status_fn is None:
            return [
                BotResponse(
                    chat_id=chat_id,
                    text="⚠️  Dead-man not configured.",
                )
            ]
        r = self.deadman_status_fn()
        status = r.get("status", "?")
        emoji = {"ok": "🟢", "warning": "🟡", "alert": "🔴"}.get(status, "⚪")
        return [
            BotResponse(
                chat_id=chat_id,
                text=(
                    f"💀 Dead-man: {emoji} {status}\n"
                    f"  last_post: {r.get('last_post_at') or '(never)'}\n"
                    f"  days_since: {r.get('days_since')}\n"
                    f"  threshold: {r.get('threshold_days')}d"
                ),
            )
        ]

    def cmd_quota(self, chat_id: int, _args: list[str]) -> list[BotResponse]:
        if self.quota_fn is None:
            return [
                BotResponse(
                    chat_id=chat_id,
                    text="⚠️  Quota not configured.",
                )
            ]
        rows = self.quota_fn()
        if not rows:
            return [BotResponse(chat_id=chat_id, text="(no quota data)")]
        lines = ["📊 Today's quota:"]
        for q in rows:
            action = q.get("action", "?")
            used = q.get("used", 0)
            limit = q.get("limit", 0)
            bar = "█" * int(q.get("percent", 0) / 10) + "░" * (
                10 - int(q.get("percent", 0) / 10)
            )
            lines.append(f"  {action:10s} {bar} {used:3d}/{limit:3d}")
        return [BotResponse(chat_id=chat_id, text="\n".join(lines))]

    def cmd_draft(self, chat_id: int, args: list[str]) -> list[BotResponse]:
        if not args:
            return [
                BotResponse(chat_id=chat_id, text="usage: /draft <topic>")
            ]
        topic = " ".join(args).strip()
        if not topic:
            return [BotResponse(chat_id=chat_id, text="(empty topic)")]
        if self.draft_fn is None:
            return [
                BotResponse(
                    chat_id=chat_id,
                    text="⚠️  Drafter not configured.",
                )
            ]
        try:
            text = self.draft_fn(topic)
        except Exception as e:
            return [
                BotResponse(chat_id=chat_id, text=f"❌ {type(e).__name__}: {e}")
            ]
        preview = text if len(text) <= 500 else text[:497] + "..."
        return [
            BotResponse(
                chat_id=chat_id,
                text=f"📝 Draft (preview):\n\n{preview}\n\n(/post to publish)",
            )
        ]

    # ------------------------------------------------------------------
    # Top-level dispatch
    # ------------------------------------------------------------------

    @classmethod
    def _register(cls) -> None:
        cls._COMMANDS["start"] = cls.cmd_start
        cls._COMMANDS["help"] = cls.cmd_help
        cls._COMMANDS["post"] = cls.cmd_post
        cls._COMMANDS["template"] = cls.cmd_template
        cls._COMMANDS["schedule"] = cls.cmd_schedule
        cls._COMMANDS["deadman"] = cls.cmd_deadman
        cls._COMMANDS["quota"] = cls.cmd_quota
        cls._COMMANDS["draft"] = cls.cmd_draft

    def handle(self, chat_id: int, text: str) -> list[BotResponse]:
        """Dispatch a free-text message. Returns 0 or more BotResponses.

        Returns an empty list if the chat isn't allowed (silent drop).
        """
        if not self.check_allowed(chat_id):
            log.debug("telegram_bot: dropping message from chat_id=%s", chat_id)
            return []
        if not text or not text.strip():
            return []
        stripped = text.strip()
        if not stripped.startswith("/"):
            return [
                BotResponse(
                    chat_id=chat_id,
                    text=(
                        "Send /help for the list of commands. "
                        "Messages must start with /."
                    ),
                )
            ]
        parts = stripped.split()
        cmd = parts[0][1:].split("@")[0]  # strip "/foo" and optional "@botname"
        args = parts[1:]
        if not cmd:
            return []
        handler = type(self)._COMMANDS.get(cmd)
        if handler is None:
            return [
                BotResponse(
                    chat_id=chat_id,
                    text=f"❓ Unknown command: /{cmd}",
                )
            ]
        try:
            return handler(self, chat_id, args)
        except Exception as e:
            log.exception("telegram_bot: handler /%s failed", cmd)
            return [
                BotResponse(
                    chat_id=chat_id,
                    text=f"❌ {type(e).__name__}: {e}",
                )
            ]


# Class-level command registry (shared across instances).
TelegramBot._COMMANDS = {}
TelegramBot._register()


# --- status / introspection -------------------------------------------------


def status(bot: TelegramBot | None = None) -> dict[str, Any]:
    """Return a snapshot of the bot's runtime state."""
    if bot is None:
        token_present = bool(os.environ.get(ENV_BOT_TOKEN, "").strip())
        raw = os.environ.get(ENV_ALLOWED_CHAT_IDS, "")
        return {
            "running": False,
            "token_present": token_present,
            "allowed_chat_ids": sorted(parse_allowed_chat_ids(raw)),
            "allowlist_empty": not parse_allowed_chat_ids(raw),
        }
    return {
        "running": True,
        "token_present": bool(os.environ.get(ENV_BOT_TOKEN, "").strip()),
        "allowed_chat_ids": sorted(bot.allowed),
        "allowlist_empty": not bot.allowed,
    }


# --- production wiring (default functions for the bot) ---------------------


def _default_post_fn(text: str) -> dict[str, Any]:
    """Default post handler: dry-run via SafetyGuard.

    This is the safe default — the bot does NOT actually publish to
    LinkedIn unless the operator wires up a real browser client. The
    dry-run path goes through the full pipeline (business hours, quota,
    warm-up) and either returns ok=True with a plan string, or raises a
    ``SafetyError`` subclass (caught here and returned as ok=False).
    """
    try:
        # Lazy import: keep this module importable in CI / tests that
        # don't have a real DB.
        from .config import load_config
        from .db import DB
        from .safety import ActionPlan, SafetyGuard, SafetyError

        cfg = load_config()
        db = DB(cfg.storage.db_path)
        try:
            guard = SafetyGuard(cfg, db)
            plan = ActionPlan(
                action="post",
                target="self",
                payload={"text": text, "visibility": "PUBLIC"},
                dry_run=True,  # always dry-run from the bot
            )
            guard.enforce(plan)
            return {"ok": True, "dry_run": True, "chars": len(text)}
        finally:
            db.close()
    except Exception as e:  # SafetyError or any DB/config issue
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _default_draft_fn(topic: str) -> str:
    """Default draft handler: uses PostDrafter if configured."""
    from .drafter import PostDrafter

    return PostDrafter().draft(topic)


def _default_list_schedules_fn() -> list[dict[str, Any]]:
    from .scheduler import PostScheduler

    rows = []
    for s in PostScheduler().list_schedules():
        rows.append(
            {
                "name": s.name,
                "enabled": s.enabled,
                "cron": s.cron,
                "at": s.at,
                "days": s.days,
                "time": s.time,
                "template": s.template,
            }
        )
    return rows


def _default_deadman_status_fn() -> dict[str, Any]:
    from .deadman import DeadManSwitch
    from .config import load_config

    cfg = load_config()
    return DeadManSwitch(Path(cfg.storage.db_path)).check()


def _default_quota_fn() -> list[dict[str, Any]]:
    from .analytics import Analytics
    from .config import load_config
    from .db import DB

    cfg = load_config()
    db = DB(cfg.storage.db_path)
    try:
        a = Analytics(db)
        s = a.quota_usage()
        # Flatten to a list of {action, used, limit, percent}
        out: list[dict[str, Any]] = []
        for item in s.get("actions", []):
            out.append(
                {
                    "action": item.get("action", "?"),
                    "used": int(item.get("used", 0)),
                    "limit": int(item.get("limit", 0)),
                    "percent": float(item.get("percent", 0.0)),
                }
            )
        return out
    finally:
        db.close()


def _default_template_render_fn(name: str, variables: dict[str, str]) -> str:
    from .templates import TemplatesStore

    return TemplatesStore().render(name=name, variables=variables)


def build_default_bot(allowed: set[int] | None = None) -> TelegramBot:
    """Build a TelegramBot with the production handlers wired up.

    Callers can override individual ``*_fn`` attributes on the returned
    object for testing or custom deployments.
    """
    if allowed is None:
        allowed = parse_allowed_chat_ids(os.environ.get(ENV_ALLOWED_CHAT_IDS))
    return TelegramBot(
        allowed=allowed,
        post_fn=_default_post_fn,
        draft_fn=_default_draft_fn,
        list_schedules_fn=_default_list_schedules_fn,
        deadman_status_fn=_default_deadman_status_fn,
        quota_fn=_default_quota_fn,
        template_render_fn=_default_template_render_fn,
    )


# --- polling loop (uses python-telegram-bot) --------------------------------


def run_telegram_bot(
    bot: TelegramBot | None = None,
    *,
    poll_timeout: int = 30,
) -> None:
    """Run the bot's polling loop. Blocks forever (Ctrl-C to stop).

    Wraps ``python-telegram-bot`` v20+. If the library is not installed,
    raises ``TelegramBotError`` with a clear message.
    """
    token = os.environ.get(ENV_BOT_TOKEN, "").strip()
    if not token:
        raise TelegramBotError(
            f"{ENV_BOT_TOKEN} is not set; cannot start the bot."
        )
    if bot is None:
        bot = build_default_bot()
    try:
        from telegram import Update
        from telegram.ext import (
            Application,
            CommandHandler,
            MessageHandler,
            filters,
        )
    except ImportError as e:
        raise TelegramBotError(
            "python-telegram-bot is not installed; "
            "install it with `pip install python-telegram-bot>=20.0`"
        ) from e

    app = Application.builder().token(token).build()

    async def _send(chat_id: int, text: str) -> None:
        await app.bot.send_message(chat_id=chat_id, text=text)

    def _sync_handler(command_name: str):
        def fn(update: "Update", _context) -> None:
            if update.effective_chat is None:
                return
            chat_id = int(update.effective_chat.id)
            text = update.effective_message.text or ""
            # Reuse the *core* dispatch — single source of truth.
            responses = bot.handle(chat_id, text)
            # Schedule async sends on the running loop.
            import asyncio
            for r in responses:
                asyncio.create_task(_send(r.chat_id, r.text))
        return fn

    # Register a single MessageHandler that routes all commands; we don't
    # need separate CommandHandlers because the core dispatcher is
    # command-aware. Using one MessageHandler also lets us catch unknown
    # /commands with a single "unknown" path.
    app.add_handler(
        MessageHandler(filters.COMMAND | filters.TEXT, _sync_handler("any"))
    )
    log.info(
        "telegram_bot: starting polling (poll_timeout=%ds, allowed=%d chats)",
        poll_timeout,
        len(bot.allowed),
    )
    app.run_polling(poll_timeout_seconds=poll_timeout)


# --- HTTP helpers (urllib — also useful for tests) --------------------------


def send_message(chat_id: int, text: str, token: str | None = None) -> bool:
    """Low-level: POST a message via the Telegram Bot API (urllib)."""
    tok = token or os.environ.get(ENV_BOT_TOKEN, "").strip()
    if not tok:
        return False
    url = f"{TELEGRAM_API_BASE}/bot{tok}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TELEGRAM_TIMEOUT) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        log.warning("telegram_bot.send_message failed: %s", e)
        return False
