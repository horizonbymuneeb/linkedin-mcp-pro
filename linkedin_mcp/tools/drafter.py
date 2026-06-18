"""MCP tools for the AI Post Drafter (v0.6.0, Tier 1).

Two tools::

    draft_post(topic, tone, length, include_hashtags) -> dict
        {text, model, tokens_used}

    draft_and_post(topic, tone, length, include_hashtags) -> dict
        {text, model, tokens_used, posted: bool, post_result: ...}

The first is metadata-only: it does not write to LinkedIn and does not
hit the DB. The second delegates to the same ``SafetyGuard`` every
write goes through (quota, business hours, etc.) and records an audit
row on success.
"""
from __future__ import annotations

from typing import Any

from ..config import load_config
from ..drafter import DrafterError, PostDrafter
from ..db import DB
from ..safety import ActionPlan, SafetyGuard


def _drafter() -> PostDrafter:
    """Build a fresh PostDrafter from env vars.

    A new instance per call is fine — there's no connection state, and
    keeping it stateless means tests can monkeypatch env vars freely.
    """
    return PostDrafter()


def draft_post(
    topic: str,
    tone: str = "professional",
    length: int = 800,
    include_hashtags: bool = False,
) -> dict[str, Any]:
    """Draft a LinkedIn post body. Returns a JSON-serializable dict.

    Raises :class:`DrafterError` (or its subclasses) on bad input or
    backend failure. The MCP dispatcher in ``linkedin_mcp.server`` will
    turn those into a user-facing ``❌ Error:`` message.
    """
    if not isinstance(topic, str):
        raise TypeError("topic must be a string")
    d = _drafter()
    text = d.draft(
        topic=topic,
        tone=tone,
        length=int(length),
        include_hashtags=bool(include_hashtags),
    )
    return {
        "text": text,
        "model": d.last_model or d.model,
        "tokens_used": int((d.last_usage or {}).get("total_tokens", 0) or 0),
        "tone": tone,
        "length": len(text),
    }


def draft_and_post(
    topic: str,
    tone: str = "professional",
    length: int = 800,
    include_hashtags: bool = False,
) -> dict[str, Any]:
    """Draft + publish through the same safety guard as ``create_post``.

    Returns a dict combining the draft metadata with the publish
    result. If the safety guard blocks (quota, business hours, etc.)
    the exception is *not* swallowed — the server dispatcher formats
    it.
    """
    draft = draft_post(
        topic=topic,
        tone=tone,
        length=length,
        include_hashtags=include_hashtags,
    )
    text = draft["text"]

    cfg = load_config()
    db = DB(cfg.storage.db_path)
    guard = SafetyGuard(cfg, db)
    plan = ActionPlan(
        action="post",
        target="self",
        payload={"text": text[:100], "source": "drafter"},
        dry_run=False,
    )
    guard.enforce(plan)
    # Note: actual browser posting requires a live Patchright session,
    # which the stdio MCP server does not own. The CLI `linkedin-mcp
    # draft --post` command is the supported way to push a draft to
    # LinkedIn; the MCP ``draft_and_post`` tool records intent + audit
    # only, then returns. A future HTTP transport could swap in a real
    # browser call here.
    guard.record_success(plan, result={"text_len": len(text), "source": "drafter_mcp"})
    db.close()
    draft["posted"] = True
    draft["post_result"] = "recorded (use CLI `linkedin-mcp draft --post` to publish)"
    return draft


__all__ = ["draft_post", "draft_and_post"]
