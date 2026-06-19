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
  - stdio (default, for any MCP-compatible client — Claude Desktop, Cursor, Windsurf, VS Code, etc.)
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
from .browser import BrowserChallenge  # re-raised in call_tool for clear UX

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
            "Publish a new LinkedIn post (text + optional local image/video file). "
            "Subject to daily quota (default 2), warm-up, business hours, jitter. "
            "Set dry_run=true to preview."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "minLength": 1, "maxLength": 3000},
                "media_path": {
                    "type": "string",
                    "description": "Optional local file path to image (.jpg/.png/.gif) or video (.mp4/.mov). Max 200MB.",
                },
                "visibility": {"type": "string", "enum": ["PUBLIC", "CONNECTIONS"], "default": "PUBLIC"},
                "dry_run": {"type": "boolean", "default": False},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "delete_post",
        "description": "Permanently delete one of your own posts by URL or URN.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "LinkedIn post URL (https://www.linkedin.com/feed/update/urn:...) OR a URN like 'urn:li:activity:1234'",
                },
                "dry_run": {"type": "boolean", "default": False},
            },
            "required": ["target"],
            "additionalProperties": False,
        },
    },
    {
        "name": "comment_on_post",
        "description": "Post a comment on a LinkedIn post (by URL or URN).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "LinkedIn post URL OR URN (urn:li:activity:...)",
                },
                "text": {"type": "string", "minLength": 1, "maxLength": 1250},
                "dry_run": {"type": "boolean", "default": False},
            },
            "required": ["target", "text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "react_to_post",
        "description": "Add a reaction (like, celebrate, insightful, etc.) to a post.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "LinkedIn post URL OR URN",
                },
                "reaction_type": {
                    "type": "string",
                    "enum": ["LIKE", "CELEBRATE", "INSIGHTFUL", "LOVE", "SUPPORT", "FUNNY", "CURIOUS", "MIND"],
                    "default": "LIKE",
                },
                "dry_run": {"type": "boolean", "default": False},
            },
            "required": ["target"],
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
    # =================== TEMPLATES (v0.5.0) ===================
    {
        "name": "list_templates",
        "description": (
            "List all saved LinkedIn post templates (name, description, tags). "
            "Templates live in ~/.linkedin-mcp/templates/ by default; override "
            "with LINKEDIN_MCP_TEMPLATES_DIR."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_template",
        "description": "Return one template's full YAML document (name, body, tags, default_vars).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Template name."},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "render_template",
        "description": (
            "Render a template's body with the given variables. "
            "Built-in variables {date}, {time}, {day_of_week}, {week_number}, "
            "{month}, {year} are auto-filled. With strict=true, missing "
            "variables raise an error instead of being left verbatim."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Template name."},
                "variables": {
                    "type": "object",
                    "description": "Variable substitution map (key -> string).",
                    "additionalProperties": {"type": "string"},
                    "default": {},
                },
                "strict": {
                    "type": "boolean",
                    "default": False,
                    "description": "If true, fail when a {var} placeholder has no value.",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "save_template",
        "description": (
            "Create or overwrite a post template. Body may contain {variable} "
            "placeholders. Tags are searchable labels; default_vars are values "
            "filled in at render time unless overridden by the caller."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "body": {"type": "string"},
                "description": {"type": "string", "default": ""},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                },
                "default_vars": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "default": {},
                },
            },
            "required": ["name", "body"],
            "additionalProperties": False,
        },
    },
    {
        "name": "delete_template",
        "description": "Delete a saved template by name.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    # =================== DEAD-MAN SWITCH (v0.5.0) ===================
    {
        "name": "deadman_status",
        "description": (
            "Read-only dead-man switch check: last_post_at, days_since, status, "
            "should_alert. No side effects, no Telegram call."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "deadman_check_and_alert",
        "description": (
            "Force a dead-man check and send a Telegram alert if status=='alert' "
            "and the 24h cooldown has elapsed. Telegram is configured via "
            "LINKEDIN_MCP_TELEGRAM_BOT_TOKEN + LINKEDIN_MCP_TELEGRAM_CHAT_ID."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "deadman_test_alert",
        "description": (
            "Send a one-off test Telegram message (bypasses the 24h cooldown) "
            "so the operator can verify bot token + chat id are wired up."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    # =================== SCHEDULER (v0.5.0) ===================
    {
        "name": "list_schedules",
        "description": "List all post schedules from ~/.linkedin-mcp/schedule.yaml.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "add_schedule",
        "description": (
            "Add a new post schedule. Provide one of: cron (5-field), at (ISO datetime), "
            "or days+time. Provide either template (name) or text (direct body)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "cron": {"type": "string"},
                "at": {"type": "string"},
                "days": {"type": "array", "items": {"type": "string"}},
                "time": {"type": "string"},
                "template": {"type": "string"},
                "text": {"type": "string"},
                "vars": {"type": "object"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "remove_schedule",
        "description": "Remove a post schedule by name.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "enable_schedule",
        "description": "Re-enable a disabled post schedule.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "disable_schedule",
        "description": "Disable a post schedule (keeps it in the YAML, won't run).",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "run_due_now",
        "description": (
            "Enqueue all currently-due schedules into the action queue. "
            "The scheduler worker drains them through SafetyGuard + create_post."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
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
    # =================== ANALYTICS (v0.6.0) ===================
    {
        "name": "get_post_volume",
        "description": (
            "Per-day count of post audits in the last ``days`` days (UTC). "
            "Days with zero posts are included so the series is dense. "
            "Returns {date: count}."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "default": 30, "minimum": 1, "maximum": 365},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_post_success_rate",
        "description": (
            "Roll-up of post outcomes in the last ``days`` days: total, "
            "success, failed, dry_run, blocked, rate. Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "default": 30, "minimum": 1, "maximum": 365},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_quota_usage",
        "description": (
            "Today's per-action-type quota usage (raw counts from "
            "daily_quotas). Caps are not joined in — use ``get_daily_stats`` "
            "for cap-aware output."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_top_posting_hours",
        "description": (
            "Distribution of post audits by hour-of-day (0..23, UTC) over "
            "the last ``days`` days. All 24 hours are present (zero-filled)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "default": 90, "minimum": 1, "maximum": 365},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_top_posting_days",
        "description": (
            "Distribution of post audits by weekday name (Monday..Sunday) "
            "over the last ``days`` days. All seven days are present (zero-filled)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "default": 90, "minimum": 1, "maximum": 365},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_recent_posts",
        "description": (
            "Most recent ``limit`` post audit rows, newest first. Each row "
            "has id, action, target, status, dry_run, detail, created_at."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 200},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_analytics_summary",
        "description": (
            "One-call roll-up: success rate, today's quota, top hour, top "
            "weekday. Read-only — no LinkedIn calls."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "default": 30, "minimum": 1, "maximum": 365},
            },
            "additionalProperties": False,
        },
    },
    # =================== v1.1.0 (Tier 3 — safety-gated) ===================
    {
        "name": "get_safety_status",
        "description": "Get the current safety gate status: config, daily/hourly usage, active pauses, cooldowns. All Tier 3 features go through this gate.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "set_safety_config",
        "description": "Update safety config. Pass any subset of: enabled, dry_run, tz, whitelist, blacklist, business_hours_start, business_hours_end, cooldown_min, cooldown_max, like_daily, comment_daily, connect_daily, feed_watch_daily, account_age_days, warmup_days, warmup_multiplier, like_hourly, comment_hourly, connect_hourly, negative_response_threshold, shadowban_pause_hours. Lists (whitelist/blacklist) are replaced entirely.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
    },
    {
        "name": "clear_safety_pause",
        "description": "Manually clear any active pauses (negative feedback / shadow-ban). Use this after reviewing and addressing the cause.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "poll_feed",
        "description": "Run a single poll cycle against the feed. Returns what was added, denied, and the safety gate decision.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "max_items": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "build_digest",
        "description": "Build a digest of the last N hours of feed activity (top posts, mentions, keyword alerts, trending, warnings).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lookback_hours": {"type": "integer", "default": 24, "minimum": 1, "maximum": 168},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_digest_markdown",
        "description": "Return the digest as Markdown text (ready to send to Telegram or email).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lookback_hours": {"type": "integer", "default": 24, "minimum": 1, "maximum": 168},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "auto_like_by_keyword",
        "description": "Search posts by keyword and like them through the safety gate. Defaults to dry-run. Each like is gated by daily quota (default 30/day), hourly quota (5/hr), whitelist/blacklist match, and randomized cooldown. Returns detailed per-post decisions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string"},
                "max_results": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
            },
            "required": ["keyword"],
            "additionalProperties": False,
        },
    },
    {
        "name": "auto_comment_by_keyword",
        "description": "Search posts by keyword and comment on them through the safety gate. VERY HIGH BAN RISK: defaults to 5/day, 1/hour, requires author in 1st-degree network, blocks spam phrases, requires personalized draft. ALWAYS start with dry_run=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string"},
                "max_results": {"type": "integer", "default": 3, "minimum": 1, "maximum": 20},
                "tone": {"type": "string", "default": "thought-leadership", "enum": ["professional", "casual", "thought-leadership", "story"]},
            },
            "required": ["keyword"],
            "additionalProperties": False,
        },
    },
    {
        "name": "auto_connect_by_criteria",
        "description": "Find people matching criteria and send connection requests through the safety gate. HIGH BAN RISK: defaults to 20/day, 3/hour, requires personalized note (no blank invites), blocks recruiters/agencies by default. ALWAYS start with dry_run=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "role": {"type": "string", "description": "e.g. 'ML Engineer'"},
                "location": {"type": "string", "description": "e.g. 'Pakistan'"},
                "keywords": {"type": "string", "description": "space-separated keywords"},
                "max_results": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "voice_to_draft",
        "description": "Transcribe an audio file (mp3/m4a/wav/ogg) via Whisper, clean filler words, and produce an AI-drafted LinkedIn post. Returns draft for human review — does NOT post automatically. Requires ffmpeg + faster-whisper on host.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "audio_path": {"type": "string", "description": "Absolute path to the audio file"},
                "language": {"type": "string", "default": "en"},
                "tone": {"type": "string", "default": "thought-leadership", "enum": ["professional", "casual", "thought-leadership", "story"]},
            },
            "required": ["audio_path"],
            "additionalProperties": False,
        },
    },
    # =================== LLM KEY MANAGEMENT (v1.2.0) ===================
    {
        "name": "llm_list_providers",
        "description": "List all configured LLM providers with status (masked keys, last test result).",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "llm_add_key",
        "description": "Add or update an LLM API key.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {"type": "string"},
                "key": {"type": "string"},
                "base_url": {"type": "string"},
                "model": {"type": "string"},
            },
            "required": ["provider"],
            "additionalProperties": False,
        },
    },
    {
        "name": "llm_remove_key",
        "description": "Remove an LLM provider's stored key.",
        "inputSchema": {
            "type": "object",
            "properties": {"provider": {"type": "string"}},
            "required": ["provider"],
            "additionalProperties": False,
        },
    },
    {
        "name": "llm_test_key",
        "description": "Test if a provider's key works (1-token ping).",
        "inputSchema": {
            "type": "object",
            "properties": {"provider": {"type": "string"}},
            "required": ["provider"],
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
            payload={"text": args["text"][:100], "media_path": args.get("media_path")},
            dry_run=dry_run,
        )
        guard.enforce(plan)
        if dry_run:
            raise DryRun(plan)
        result = await br.create_post(
            text=args["text"],
            media_path=args.get("media_path"),
            visibility=args.get("visibility", "PUBLIC"),
        )
    elif name == "delete_post":
        plan = ActionPlan(action="post", target=args["target"], payload={"op": "delete"}, dry_run=dry_run)
        guard.enforce(plan)
        if dry_run:
            raise DryRun(plan)
        result = await br.delete_post(target=args["target"])
    elif name == "comment_on_post":
        plan = ActionPlan(
            action="comment",
            target=args["target"],
            payload={"text": args["text"][:100]},
            dry_run=dry_run,
        )
        guard.enforce(plan)
        if dry_run:
            raise DryRun(plan)
        result = await br.comment_on_post(target=args["target"], text=args["text"])
    elif name == "react_to_post":
        plan = ActionPlan(
            action="reaction",
            target=args["target"],
            payload={"type": args.get("reaction_type", "LIKE")},
            dry_run=dry_run,
        )
        guard.enforce(plan)
        if dry_run:
            raise DryRun(plan)
        result = await br.react_to_post(
            target=args["target"], reaction_type=args.get("reaction_type", "LIKE")
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


async def _dispatch_deadman(name: str, args: dict) -> Any:
    """Dispatcher for the dead-man-switch tools (v0.5.0).

    All three are read- or notification-only — no SafetyGuard needed.
    """
    from .tools import deadman as _dm_tools

    if name == "deadman_status":
        return _dm_tools.deadman_status()
    if name == "deadman_check_and_alert":
        return _dm_tools.deadman_check_and_alert()
    if name == "deadman_test_alert":
        return _dm_tools.deadman_test_alert()
    raise ValueError(f"Unknown deadman tool: {name}")


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


async def _dispatch_templates(name: str, args: dict) -> Any:
    """Dispatcher for the post-template tools (v0.5.0).

    All five tools are metadata-only — they touch the filesystem, not
    LinkedIn — so they bypass the SafetyGuard. Use ``render_template``
    + ``create_post`` (which IS safety-enforced) to actually publish.
    """
    # Lazy import keeps server importable even if PyYAML is missing
    # (templates module pulls in yaml).
    from .tools import templates as _tpl_tools

    if name == "list_templates":
        return _tpl_tools.list_templates()
    if name == "get_template":
        return _tpl_tools.get_template(args["name"])
    if name == "render_template":
        return _tpl_tools.render_template(
            name=args["name"],
            variables=args.get("variables") or {},
            strict=bool(args.get("strict", False)),
        )
    if name == "save_template":
        return _tpl_tools.save_template(
            name=args["name"],
            body=args["body"],
            description=args.get("description", ""),
            tags=args.get("tags") or [],
            default_vars=args.get("default_vars") or {},
        )
    if name == "delete_template":
        return _tpl_tools.delete_template(args["name"])
    raise ValueError(f"Unknown template tool: {name}")


async def _dispatch_scheduler(name: str, args: dict) -> Any:
    """Dispatcher for the post-scheduler tools (v0.5.0)."""
    from .tools import scheduler as _sch_tools
    if name == "list_schedules":
        return _sch_tools.list_schedules()
    if name == "add_schedule":
        return _sch_tools.add_schedule(
            name=args["name"],
            cron=args.get("cron"),
            at=args.get("at"),
            days=args.get("days"),
            time=args.get("time"),
            template=args.get("template"),
            text=args.get("text"),
            vars=args.get("vars"),
            tags=args.get("tags"),
        )
    if name == "remove_schedule":
        return _sch_tools.remove_schedule(args["name"])
    if name == "enable_schedule":
        return _sch_tools.enable_schedule(args["name"])
    if name == "disable_schedule":
        return _sch_tools.disable_schedule(args["name"])
    if name == "run_due_now":
        return _sch_tools.run_due_now()
    raise ValueError(f"Unknown scheduler tool: {name}")


async def _dispatch_analytics(name: str, args: dict) -> Any:
    """Dispatcher for the post-analytics tools (v0.6.0).

    All seven are read-only — they touch the local audit_log +
    daily_quotas tables, never the network — so they bypass the
    SafetyGuard. They share the long-lived DB from ``state()`` so we
    don't open a new connection per call.
    """
    from .analytics import Analytics
    from .tools import analytics as _an_tools
    from .tools import best_time as _bt_tools
    from .tools import multi_account as _acc_tools

    _, db, _ = state()
    a = Analytics(db)
    if name == "get_post_volume":
        return a.post_volume(days=args.get("days", 30))
    if name == "get_post_success_rate":
        return a.post_success_rate(days=args.get("days", 30))
    if name == "get_quota_usage":
        return a.quota_usage()
    if name == "get_top_posting_hours":
        return a.top_posting_hours(days=args.get("days", 90))
    if name == "get_top_posting_days":
        return a.top_posting_days(days=args.get("days", 90))
    if name == "get_recent_posts":
        return a.recent_posts(limit=args.get("limit", 10))
    if name == "get_analytics_summary":
        return a.summary(days=args.get("days", 30))
    raise ValueError(f"Unknown analytics tool: {name}")


async def _dispatch_accounts(name: str, args: dict) -> Any:
    """Dispatcher for the multi-account tools (v0.6.0)."""
    from .tools import multi_account as _acc_tools
    if name == "list_accounts":
        return _acc_tools.list_accounts()
    if name == "register_account":
        return _acc_tools.register_account(
            name=args["name"],
            profile_dir=args["profile_dir"],
            description=args.get("description", ""),
        )
    if name == "remove_account":
        return _acc_tools.remove_account(args["name"])
    if name == "set_active_account":
        return _acc_tools.set_active_account(args["name"])
    if name == "get_active_account":
        return _acc_tools.get_active_account()
    raise ValueError(f"Unknown accounts tool: {name}")


async def _dispatch_ab_tests(name: str, args: dict) -> Any:
    """Dispatcher for A/B test tools (v0.6.0)."""
    from .tools import ab_testing as _ab_tools
    if name == "list_ab_tests":
        return _ab_tools.list_ab_tests()
    if name == "create_ab_test":
        return _ab_tools.create_ab_test(
            name=args["name"],
            variant_a_text=args["variant_a_text"],
            variant_b_text=args["variant_b_text"],
            target_impressions=int(args.get("target_impressions", 100)),
        )
    if name == "record_ab_impressions":
        return _ab_tools.record_ab_impressions(args["name"], args["variant"], int(args["n"]))
    if name == "record_ab_engagement":
        return _ab_tools.record_ab_engagement(args["name"], args["variant"], int(args["n"]))
    if name == "get_ab_test_result":
        return _ab_tools.get_ab_test_result(args["name"])
    raise ValueError(f"Unknown ab-tests tool: {name}")


async def _dispatch_rss(name: str, args: dict) -> Any:
    """Dispatcher for RSS poster tools (v0.6.0)."""
    from .tools import rss_poster as _rss_tools
    if name == "list_rss_feeds":
        return _rss_tools.list_rss_feeds()
    if name == "add_rss_feed":
        return _rss_tools.add_rss_feed(
            name=args["name"],
            url=args["url"],
            template=args.get("template"),
            text_prefix=args.get("text_prefix", ""),
            max_per_day=int(args.get("max_per_day", 1)),
        )
    if name == "remove_rss_feed":
        return _rss_tools.remove_rss_feed(args["name"])
    if name == "poll_rss_feeds":
        return _rss_tools.poll_rss_feeds(limit_per_feed=int(args.get("limit_per_feed", 3)))
    raise ValueError(f"Unknown rss tool: {name}")


async def _dispatch_v1(name: str, args: dict) -> Any:
    """Dispatcher for the v1.0.0 feature set (webhooks, multi-platform, coach,
    calendar, leads, competitor).
    """
    from .tools import v1_features as _v1
    if name == "list_webhooks":
        return _v1.list_webhooks()
    if name == "add_webhook":
        return _v1.add_webhook(
            name=args["name"], url=args["url"],
            events=args["events"], secret=args.get("secret", ""),
        )
    if name == "remove_webhook":
        return _v1.remove_webhook(args["name"])
    if name == "fire_webhook":
        return _v1.fire_webhook(args["event"], args.get("payload") or {})
    if name == "list_platforms":
        return _v1.list_platforms_tool()
    if name == "cross_post":
        return _v1.cross_post_tool(
            text=args["text"],
            platforms=args["platforms"],
            link=args.get("link", ""),
        )
    if name == "get_coaching_report":
        return _v1.get_coaching_report(days=int(args.get("days", 30)))
    if name == "list_calendar_entries":
        return _v1.list_calendar_entries(args.get("month", ""), args.get("status", ""))
    if name == "add_calendar_entry":
        return _v1.add_calendar_entry(
            date=args["date"], title=args["title"],
            body=args.get("body", ""), status=args.get("status", "idea"),
            tags=args.get("tags") or [],
        )
    if name == "update_calendar_status":
        return _v1.update_calendar_status(args["date"], args["title"], args["new_status"])
    if name == "get_calendar_summary":
        return _v1.get_calendar_summary(args["month"])
    if name == "list_leads":
        return _v1.list_leads()
    if name == "add_lead":
        return _v1.add_lead(
            name=args["name"], profile_url=args["profile_url"],
            title=args.get("title", ""), company=args.get("company", ""),
            location=args.get("location", ""),
            tags=args.get("tags") or [], notes=args.get("notes", ""),
        )
    if name == "export_leads_csv":
        return _v1.export_leads_csv(args.get("tag", ""), args.get("company", ""))
    if name == "list_competitors_tool":
        return _v1.list_competitors_tool()
    if name == "add_competitor_tool":
        return _v1.add_competitor_tool(args["name"], args["profile_url"], args.get("notes", ""))
    if name == "add_competitor_post_tool":
        return _v1.add_competitor_post_tool(
            competitor=args["competitor"], url=args["url"], title=args["title"],
            impressions=int(args.get("impressions", 0)),
            reactions=int(args.get("reactions", 0)),
            comments=int(args.get("comments", 0)),
        )
    if name == "get_competitor_report_tool":
        return _v1.get_competitor_report_tool(days=int(args.get("days", 7)))
    raise ValueError(f"Unknown v1 tool: {name}")


# ----------------------------------------------------------------------------
# v1.1.0 (Tier 3) dispatcher — safety-gated auto-engagement + voice + digest
# ----------------------------------------------------------------------------

V1_1_TOOL_NAMES = {
    "get_safety_status", "set_safety_config", "clear_safety_pause",
    "poll_feed", "build_digest", "get_digest_markdown",
    "auto_like_by_keyword",
    "auto_comment_by_keyword",
    "auto_connect_by_criteria",
    "voice_to_draft",
}


async def _dispatch_v1_1(name: str, args: dict) -> Any:
    """Dispatcher for the v1.1.0 feature set (Tier 3 — safety-gated)."""
    from .tools import v1_1_features as _v1_1
    if name == "get_safety_status":
        return _v1_1.get_safety_status()
    if name == "set_safety_config":
        return _v1_1.set_safety_config(**args)
    if name == "clear_safety_pause":
        return _v1_1.clear_safety_pause()
    if name == "poll_feed":
        return _v1_1.poll_feed(max_items=int(args.get("max_items", 20)))
    if name == "build_digest":
        return _v1_1.build_digest(lookback_hours=int(args.get("lookback_hours", 24)))
    if name == "get_digest_markdown":
        return _v1_1.get_digest_markdown(
            lookback_hours=int(args.get("lookback_hours", 24))
        )
    if name == "auto_like_by_keyword":
        return _v1_1.auto_like_by_keyword(
            keyword=args["keyword"],
            max_results=int(args.get("max_results", 10)),
        )
    if name == "auto_comment_by_keyword":
        return _v1_1.auto_comment_by_keyword(
            keyword=args["keyword"],
            max_results=int(args.get("max_results", 3)),
            tone=args.get("tone", "thought-leadership"),
        )
    if name == "auto_connect_by_criteria":
        return _v1_1.auto_connect_by_criteria(
            role=args.get("role", ""),
            location=args.get("location", ""),
            keywords=args.get("keywords", ""),
            max_results=int(args.get("max_results", 10)),
        )
    if name == "voice_to_draft":
        return _v1_1.voice_to_draft(
            audio_path=args["audio_path"],
            language=args.get("language", "en"),
            tone=args.get("tone", "thought-leadership"),
        )
    raise ValueError(f"Unknown v1.1 tool: {name}")


def _dispatch_llm(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Dispatcher for LLM key management tools (v1.2.0)."""
    from .llm_keys import add_provider, list_providers, remove_provider, check_provider
    if name == "llm_list_providers":
        return {"providers": list_providers()}
    if name == "llm_add_key":
        return add_provider(
            name=args["provider"],
            key=args.get("key"),
            base_url=args.get("base_url"),
            model=args.get("model"),
        )
    if name == "llm_remove_key":
        return {"removed": remove_provider(args["provider"])}
    if name == "llm_test_key":
        return check_provider(args["provider"])
    return {"error": f"unknown llm tool: {name}"}


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
        # Templates (v0.5.0) — metadata-only, no safety guard.
        elif name in (
            "list_templates",
            "get_template",
            "render_template",
            "save_template",
            "delete_template",
        ):
            data = await _dispatch_templates(name, arguments)
        # Dead-man switch (v0.5.0) — DB + Telegram, no safety guard.
        elif name in (
            "deadman_status",
            "deadman_check_and_alert",
            "deadman_test_alert",
        ):
            data = await _dispatch_deadman(name, arguments)
        # Scheduler (v0.5.0) — YAML + DB, no safety guard (worker enforces).
        elif name in (
            "list_schedules",
            "add_schedule",
            "remove_schedule",
            "enable_schedule",
            "disable_schedule",
            "run_due_now",
        ):
            data = await _dispatch_scheduler(name, arguments)
        # Analytics (v0.6.0) — read-only DB queries, no safety guard.
        elif name in (
            "get_post_volume",
            "get_post_success_rate",
            "get_quota_usage",
            "get_top_posting_hours",
            "get_top_posting_days",
            "get_recent_posts",
            "get_analytics_summary",
        ):
            data = await _dispatch_analytics(name, arguments)
        # Best-time analyzer (v0.6.0) — read-only, no safety guard.
        elif name == "get_best_posting_times":
            data = _bt_tools.get_best_posting_times(days=int(arguments.get("days", 90)))
        # Multi-account (v0.6.0) — file-backed, no safety guard.
        elif name in (
            "list_accounts",
            "register_account",
            "remove_account",
            "set_active_account",
            "get_active_account",
        ):
            data = await _dispatch_accounts(name, arguments)
        # Telegram bot (v0.6.0) — introspection only; running bot is via CLI.
        elif name == "telegram_bot_status":
            from .telegram_bot import TelegramBot
            bot = TelegramBot()
            data = {"ok": True, "status": bot.status()}
        # A/B testing (v0.6.0) — file-backed, no safety guard.
        elif name in (
            "list_ab_tests",
            "create_ab_test",
            "record_ab_impressions",
            "record_ab_engagement",
            "get_ab_test_result",
        ):
            data = await _dispatch_ab_tests(name, arguments)
        # RSS auto-posts (v0.6.0) — file-backed, network access only on poll().
        elif name in (
            "list_rss_feeds",
            "add_rss_feed",
            "remove_rss_feed",
            "poll_rss_feeds",
        ):
            data = await _dispatch_rss(name, arguments)
        # Shadow-ban detector (v0.7.0) — read-only.
        elif name in (
            "check_shadowban",
            "record_post_metrics",
        ):
            from .tools import shadowban as _sb_tools
            if name == "check_shadowban":
                data = _sb_tools.check_shadowban(
                    drop_threshold=float(arguments.get("drop_threshold", 0.50)),
                    min_baseline_posts=int(arguments.get("min_baseline_posts", 5)),
                )
            else:
                data = _sb_tools.record_post_metrics(
                    target=arguments["target"],
                    impressions=int(arguments["impressions"]),
                    engagement=int(arguments["engagement"]),
                )
        # Carousel generator (v1.0.0) — text-only fallback if Pillow absent.
        elif name == "generate_carousel":
            from .tools import carousel as _car_tools
            data = _car_tools.generate_carousel(
                text=arguments["text"],
                title=arguments.get("title", ""),
                max_chars_per_slide=int(arguments.get("max_chars_per_slide", 280)),
            )
        # v1.0.0 features — webhooks, multi-platform, coach, calendar, leads, competitor
        elif name in (
            "list_webhooks", "add_webhook", "remove_webhook", "fire_webhook",
            "list_platforms", "cross_post",
            "get_coaching_report",
            "list_calendar_entries", "add_calendar_entry", "update_calendar_status", "get_calendar_summary",
            "list_leads", "add_lead", "export_leads_csv",
            "list_competitors_tool", "add_competitor_tool", "add_competitor_post_tool", "get_competitor_report_tool",
        ):
            data = await _dispatch_v1(name, arguments)
        # v1.1.0 features — safety-gated Tier 3 (auto-like, auto-comment,
        # auto-connect, voice-to-post, feed listener, digest)
        elif name in V1_1_TOOL_NAMES:
            data = await _dispatch_v1_1(name, arguments)
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
    except BrowserChallenge as e:
        # v0.3.0: browser window is still open. User must complete the
        # challenge in-place, then retry the same command.
        return [TextContent(
            type="text",
            text=f"🛡️ LinkedIn security challenge: {e}\n"
                 f"  Action: complete the challenge in the open browser window, "
                 f"then re-run this command.",
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
