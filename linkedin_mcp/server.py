"""
linkedin-mcp-pro — MCP server implementation.

Exposes 22 tools for LinkedIn:
  - 12 read tools (API-based, no ban risk)
  - 10 write tools (browser-based, full safety enforcement)
  - 1 stats tool (DB-backed)

Architecture:
  - All tools are async functions decorated with @server.list_tool() / @server.call_tool()
  - Read tools -> linkedin_mcp.api.* (Voyager HTTP)
  - Write tools -> linkedin_mcp.browser.* (Patchright)
  - Every write goes through SafetyGuard.enforce() before real execution
  - Audit log records every action (dry-run or real)

Run modes:
  - stdio (default, for Claude Desktop / Cursor / Windsurf)
  - streamable-http (for remote clients, set MCP_TRANSPORT=streamable-http)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# Project modules
from .config import Config, load_config
from .db import DB
from .safety import (
    ActionPlan,
    CaptchaDetectedError,
    DryRun,
    OutsideBusinessHoursError,
    QuotaExceededError,
    RateLimitedError,
    SafetyGuard,
    detect_captcha_in_text,
)

# Read API (Voyager) — imported lazily because Agent 1 may still be building it.
_api_module = None


def _get_api():
    """Lazy import of the API module so server boots even if it's not built yet."""
    global _api_module
    if _api_module is None:
        try:
            from . import api as _api_module
        except ImportError as e:
            log.warning("linkedin_mcp.api not yet built: %s", e)
            raise
    return _api_module


# Write (Browser) — same lazy pattern for Agent 2.
_browser_module = None


def _get_browser():
    global _browser_module
    if _browser_module is None:
        try:
            from . import browser as _browser_module
        except ImportError as e:
            log.warning("linkedin_mcp.browser not yet built: %s", e)
            raise
    return _browser_module


log = logging.getLogger("linkedin_mcp.server")


# ----------------------------------------------------------------------------
# Server singleton + state
# ----------------------------------------------------------------------------

server = Server("linkedin-mcp-pro")

# Initialized in lifespan()
_cfg: Config | None = None
_db: DB | None = None
_guard: SafetyGuard | None = None
_voyager = None  # VoyagerClient instance (lazy)
_browser = None  # BrowserClient instance (lazy)


def state() -> tuple[Config, DB, SafetyGuard]:
    assert _cfg and _db and _guard, "Server not initialized"
    return _cfg, _db, _guard


# ----------------------------------------------------------------------------
# Tool definitions
# ----------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    # =================== READS (API) ===================
    {
        "name": "get_my_profile",
        "description": "Get the authenticated user's LinkedIn profile (name, headline, summary, current position).",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_person_profile",
        "description": "Get a LinkedIn member's profile by their public identifier (vanity name).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "public_id": {
                    "type": "string",
                    "description": "The vanity name from a profile URL, e.g. 'satyam-code' from https://linkedin.com/in/satyam-code",
                },
            },
            "required": ["public_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_people",
        "description": "Search for LinkedIn members by keyword, with optional location and company filters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keywords": {"type": "string"},
                "location": {"type": "string", "description": "e.g. 'San Francisco'"},
                "current_company": {"type": "string"},
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
            },
            "required": ["keywords"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_jobs",
        "description": "Search for jobs by keyword, with optional location and experience filters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keywords": {"type": "string"},
                "location": {"type": "string"},
                "experience_level": {
                    "type": "string",
                    "enum": ["internship", "entry", "associate", "mid-senior", "director", "executive"],
                },
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
            },
            "required": ["keywords"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_job_details",
        "description": "Get full details for a specific job posting by its ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "LinkedIn job ID (numeric string)"},
            },
            "required": ["job_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_companies",
        "description": "Search for LinkedIn company pages by keyword.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keywords": {"type": "string"},
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
            },
            "required": ["keywords"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_company_profile",
        "description": "Get a LinkedIn company page profile.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "company_id": {"type": "string", "description": "LinkedIn company ID (numeric string)"},
            },
            "required": ["company_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_company_employees",
        "description": "List employees at a company from the /people/ page.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "company_id": {"type": "string"},
                "keyword": {"type": "string", "description": "Optional filter, e.g. 'engineer'"},
                "limit": {"type": "integer", "default": 25, "minimum": 1, "maximum": 100},
            },
            "required": ["company_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_feed",
        "description": "Get recent posts from your home feed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_inbox",
        "description": "List recent conversations from your LinkedIn messaging inbox.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 50},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_conversation",
        "description": "Read a specific messaging conversation by participant public ID or thread URN.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "public_id": {"type": "string", "description": "Vanity name of the other party"},
                "thread_urn": {"type": "string", "description": "Alternative: full thread URN"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_pending_invitations",
        "description": "List sent connection invitations awaiting response.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    # =================== WRITES (Browser, safety-enforced) ===================
    {
        "name": "send_connection_request",
        "description": (
            "Send a connection request to a LinkedIn member, with optional personalized note. "
            "Subject to daily quota (default 20), warm-up ramp, business hours, and jitter. "
            "Set dry_run=true to preview without sending."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "public_id": {"type": "string", "description": "Vanity name of target, e.g. 'satyam-code'"},
                "note": {"type": "string", "maxLength": 300, "description": "Personalized note (max 300 chars)"},
                "dry_run": {"type": "boolean", "default": False},
            },
            "required": ["public_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_post",
        "description": (
            "Publish a new LinkedIn post (text, optional media URL). "
            "Subject to daily quota (default 2), warm-up, business hours, jitter. "
            "Set dry_run=true to preview."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "minLength": 1, "maxLength": 3000},
                "media_url": {"type": "string", "description": "Optional image/video URL"},
                "visibility": {"type": "string", "enum": ["PUBLIC", "CONNECTIONS"], "default": "PUBLIC"},
                "dry_run": {"type": "boolean", "default": False},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "delete_post",
        "description": "Permanently delete one of your own posts by its URN/ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "post_urn": {"type": "string", "description": "LinkedIn post URN, e.g. 'urn:li:activity:1234'"},
                "dry_run": {"type": "boolean", "default": False},
            },
            "required": ["post_urn"],
            "additionalProperties": False,
        },
    },
    {
        "name": "comment_on_post",
        "description": "Post a comment on a LinkedIn post (by post URN or URL).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "post_urn": {"type": "string"},
                "text": {"type": "string", "minLength": 1, "maxLength": 1250},
                "dry_run": {"type": "boolean", "default": False},
            },
            "required": ["post_urn", "text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "react_to_post",
        "description": "Add a reaction (like, celebrate, insightful, etc.) to a post.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "post_urn": {"type": "string"},
                "reaction_type": {
                    "type": "string",
                    "enum": ["LIKE", "CELEBRATE", "INSIGHTFUL", "LOVE", "SUPPORT", "FUNNY"],
                    "default": "LIKE",
                },
                "dry_run": {"type": "boolean", "default": False},
            },
            "required": ["post_urn"],
            "additionalProperties": False,
        },
    },
    {
        "name": "send_message",
        "description": "Send a direct message to a LinkedIn member you're connected with.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "public_id": {"type": "string"},
                "text": {"type": "string", "minLength": 1, "maxLength": 8000},
                "dry_run": {"type": "boolean", "default": False},
            },
            "required": ["public_id", "text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "accept_invitation",
        "description": "Accept an incoming connection invitation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "invitation_id": {"type": "string"},
                "dry_run": {"type": "boolean", "default": False},
            },
            "required": ["invitation_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "decline_invitation",
        "description": "Decline an incoming connection invitation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "invitation_id": {"type": "string"},
                "dry_run": {"type": "boolean", "default": False},
            },
            "required": ["invitation_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "withdraw_invitation",
        "description": "Withdraw a sent (still pending) connection invitation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "invitation_id": {"type": "string"},
                "dry_run": {"type": "boolean", "default": False},
            },
            "required": ["invitation_id"],
            "additionalProperties": False,
        },
    },
    # =================== STATS ===================
    {
        "name": "get_daily_stats",
        "description": "Get today's quota usage for all action types (used / limit / zone).",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_audit_log",
        "description": "Get the most recent audit log entries (every action, with status and details).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "Filter by action type, e.g. 'connection'"},
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 200},
            },
            "additionalProperties": False,
        },
    },
]


# ----------------------------------------------------------------------------
# Tool dispatchers
# ----------------------------------------------------------------------------

async def _with_voyager(fn, *args, **kwargs):
    """Run an API function with a fresh VoyagerClient (async context)."""
    cfg, db, _ = state()
    api = _get_api()
    if not cfg.li_at:
        raise ValueError("LI_AT not configured")
    async with api.VoyagerClient(li_at=cfg.li_at, jsessionid=cfg.jsessionid, db=db) as client:
        return await fn(client, *args, **kwargs)


async def _dispatch_read(name: str, args: dict) -> Any:
    """Dispatch a read tool to the API module."""
    api = _get_api()

    if name == "get_my_profile":
        return await _with_voyager(api.get_my_profile)
    if name == "get_person_profile":
        return await _with_voyager(api.get_person_profile, args["public_id"])
    if name == "search_people":
        # Translate user-friendly location/current_company to the filters dict
        filters = {}
        if args.get("location"):
            filters["geoRegion"] = args["location"]
        if args.get("current_company"):
            filters["currentCompany"] = args["current_company"]
        return await _with_voyager(
            api.search_people,
            keywords=args["keywords"],
            count=args.get("limit", 10),
            filters=filters or None,
        )
    if name == "search_jobs":
        return await _with_voyager(
            api.search_jobs,
            keywords=args["keywords"],
            location=args.get("location"),
            count=args.get("limit", 10),
        )
    if name == "get_job_details":
        return await _with_voyager(api.get_job_details, args["job_id"])
    if name == "search_companies":
        return await _with_voyager(
            api.search_companies, args["keywords"], count=args.get("limit", 10)
        )
    if name == "get_company_profile":
        return await _with_voyager(api.get_company_profile, args["company_id"])
    if name == "get_company_employees":
        return await _with_voyager(
            api.get_company_employees,
            args["company_id"],
            count=args.get("limit", 20),
        )
    if name == "get_feed":
        return await _with_voyager(api.get_feed, count=args.get("count", 20))
    if name == "get_inbox":
        return await _with_voyager(api.get_inbox, count=args.get("limit", 20))
    if name == "get_conversation":
        # accept either public_id (will be resolved upstream) or thread_urn/conversation_id
        cid = args.get("conversation_id") or args.get("thread_urn") or args.get("public_id")
        return await _with_voyager(api.get_conversation, cid)
    if name == "get_pending_invitations":
        return await _with_voyager(api.get_pending_invitations, count=50)
    raise ValueError(f"Unknown read tool: {name}")


async def _dispatch_write(name: str, args: dict) -> dict:
    """Dispatch a write tool through the safety layer to the browser module."""
    cfg, db, guard = state()
    br = _get_browser()

    dry_run = bool(args.get("dry_run", False))

    # Build plan per tool
    if name == "send_connection_request":
        plan = ActionPlan(
            action="connection",
            target=f"linkedin.com/in/{args['public_id']}",
            payload={"note": args.get("note", "")},
            dry_run=dry_run,
        )
        guard.enforce(plan)
        if dry_run:
            raise DryRun(plan)  # already audited, just bubble
        result = await br.send_connection_request(
            public_id=args["public_id"], note=args.get("note", "")
        )
    elif name == "create_post":
        plan = ActionPlan(
            action="post",
            target="self",
            payload={"text": args["text"][:100], "media_url": args.get("media_url")},
            dry_run=dry_run,
        )
        guard.enforce(plan)
        if dry_run:
            raise DryRun(plan)
        result = await br.create_post(
            text=args["text"],
            media_url=args.get("media_url"),
            visibility=args.get("visibility", "PUBLIC"),
        )
    elif name == "delete_post":
        plan = ActionPlan(action="post", target=args["post_urn"], payload={}, dry_run=dry_run)
        guard.enforce(plan)
        if dry_run:
            raise DryRun(plan)
        result = await br.delete_post(post_urn=args["post_urn"])
    elif name == "comment_on_post":
        plan = ActionPlan(
            action="comment",
            target=args["post_urn"],
            payload={"text": args["text"][:100]},
            dry_run=dry_run,
        )
        guard.enforce(plan)
        if dry_run:
            raise DryRun(plan)
        result = await br.comment_on_post(post_urn=args["post_urn"], text=args["text"])
    elif name == "react_to_post":
        plan = ActionPlan(
            action="reaction",
            target=args["post_urn"],
            payload={"type": args.get("reaction_type", "LIKE")},
            dry_run=dry_run,
        )
        guard.enforce(plan)
        if dry_run:
            raise DryRun(plan)
        result = await br.react_to_post(
            post_urn=args["post_urn"], reaction_type=args.get("reaction_type", "LIKE")
        )
    elif name == "send_message":
        plan = ActionPlan(
            action="message",
            target=f"linkedin.com/in/{args['public_id']}",
            payload={"text": args["text"][:100]},
            dry_run=dry_run,
        )
        guard.enforce(plan)
        if dry_run:
            raise DryRun(plan)
        result = await br.send_message(public_id=args["public_id"], text=args["text"])
    elif name == "accept_invitation":
        plan = ActionPlan(action="connection", target=args["invitation_id"], payload={"op": "accept"}, dry_run=dry_run)
        guard.enforce(plan)
        if dry_run:
            raise DryRun(plan)
        result = await br.accept_invitation(invitation_id=args["invitation_id"])
    elif name == "decline_invitation":
        plan = ActionPlan(action="connection", target=args["invitation_id"], payload={"op": "decline"}, dry_run=dry_run)
        guard.enforce(plan)
        if dry_run:
            raise DryRun(plan)
        result = await br.decline_invitation(invitation_id=args["invitation_id"])
    elif name == "withdraw_invitation":
        plan = ActionPlan(action="connection", target=args["invitation_id"], payload={"op": "withdraw"}, dry_run=dry_run)
        guard.enforce(plan)
        if dry_run:
            raise DryRun(plan)
        result = await br.withdraw_invitation(invitation_id=args["invitation_id"])
    else:
        raise ValueError(f"Unknown write tool: {name}")

    guard.record_success(plan, result=result)
    return result


async def _dispatch_stats(name: str, args: dict) -> Any:
    cfg, db, _ = state()
    if name == "get_daily_stats":
        limits = {
            "connection": cfg.safety.daily_limit_connection_requests,
            "post": cfg.safety.daily_limit_posts,
            "message": cfg.safety.daily_limit_messages,
            "comment": cfg.safety.daily_limit_comments,
            "reaction": cfg.safety.daily_limit_reactions,
        }
        return [
            {
                "action": q.action,
                "used": q.used,
                "limit": q.limit,
                "remaining": q.remaining,
                "zone": q.zone,
                "day": q.day,
            }
            for q in db.get_all_quotas(limits)
        ]
    if name == "get_audit_log":
        return db.get_audit(action=args.get("action"), limit=args.get("limit", 20))
    raise ValueError(f"Unknown stats tool: {name}")


# ----------------------------------------------------------------------------
# MCP server wiring
# ----------------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name=t["name"],
            description=t["description"],
            inputSchema=t["inputSchema"],
        )
        for t in TOOLS
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Route tool call to read, write, or stats dispatcher."""
    try:
        # Read tools
        if name in (
            "get_my_profile", "get_person_profile", "search_people", "search_jobs",
            "get_job_details", "search_companies", "get_company_profile",
            "get_company_employees", "get_feed", "get_inbox", "get_conversation",
            "get_pending_invitations",
        ):
            data = await _dispatch_read(name, arguments)
        # Write tools
        elif name in (
            "send_connection_request", "create_post", "delete_post",
            "comment_on_post", "react_to_post", "send_message",
            "accept_invitation", "decline_invitation", "withdraw_invitation",
        ):
            data = await _dispatch_write(name, arguments)
        # Stats
        elif name in ("get_daily_stats", "get_audit_log"):
            data = await _dispatch_stats(name, arguments)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

        # Render response
        if isinstance(data, (dict, list)):
            text = json.dumps(data, indent=2, default=str)
        else:
            text = str(data)
        return [TextContent(type="text", text=text)]

    except DryRun as e:
        return [TextContent(type="text", text=f"✓ {e.plan}")]
    except QuotaExceededError as e:
        return [TextContent(type="text", text=f"⛔ Quota: {e.reason}")]
    except OutsideBusinessHoursError as e:
        return [TextContent(type="text", text=f"⏰ Hours: {e.reason}")]
    except RateLimitedError as e:
        msg = f"🐌 Rate limit: {e.reason}"
        if e.retry_after_seconds:
            msg += f"\n  Retry in: {e.retry_after_seconds}s"
        return [TextContent(type="text", text=msg)]
    except CaptchaDetectedError as e:
        return [TextContent(
            type="text",
            text=f"🤖 CAPTCHA detected: {e.reason}\n  Action: all writes paused 24h. Solve in browser.",
        )]
    except Exception as e:
        log.exception("Tool call failed: %s", name)
        return [TextContent(type="text", text=f"❌ Error: {type(e).__name__}: {e}")]


# ----------------------------------------------------------------------------
# Lifespan (init / cleanup)
# ----------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app):
    global _cfg, _db, _guard
    _cfg = load_config()
    errors = _cfg.validate()
    if errors:
        for e in errors:
            log.error("Config: %s", e)
        raise SystemExit(f"Config invalid: {'; '.join(errors)}")

    logging.basicConfig(
        level=getattr(logging, _cfg.server.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    _db = DB(_cfg.storage.db_path)
    _guard = SafetyGuard(_cfg, _db)
    log.info("linkedin-mcp-pro started. DB: %s", _cfg.storage.db_path)
    log.info("Daily limits: 20 conn / 2 posts / 30 msg / 30 comments")
    log.info("Business hours: %02d:00-%02d:00 UTC, %s",
             _cfg.safety.business_hours_start, _cfg.safety.business_hours_end,
             ", ".join(_cfg.safety.business_days))
    try:
        yield
    finally:
        log.info("Shutting down...")
        if _db:
            _db.close()


# ----------------------------------------------------------------------------
# Entrypoint
# ----------------------------------------------------------------------------

async def _run_stdio():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    # Graceful shutdown
    def _sigterm(*_):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _sigterm)

    global _cfg, _db, _guard
    if _cfg is None:
        # Initialize for non-stdio path; stdio will re-init via lifespan
        _cfg = load_config()
        _db = DB(_cfg.storage.db_path)
        _guard = SafetyGuard(_cfg, _db)

    if _cfg.server.transport == "stdio":
        asyncio.run(_run_stdio())
    elif _cfg.server.transport == "streamable-http":
        from mcp.server import streamable_http
        # Future: HTTP transport support
        log.error("streamable-http not yet implemented")
        sys.exit(1)
    else:
        log.error("Unknown transport: %s", _cfg.server.transport)
        sys.exit(1)


if __name__ == "__main__":
    main()
