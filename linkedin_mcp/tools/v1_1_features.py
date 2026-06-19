"""MCP tool wrappers for v1.1.0 (Tier 3) features.

These wrap the safety-gated Tier 3 features as MCP tools. All
write-side actions (like, comment, connect) go through SafetyGate
via the new safety module.

Read-side tools (digest, build, transcription) are safe to expose
without additional gating.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _profile_dir() -> Path:
    """Return the user's profile directory."""
    from .config import load_config
    cfg = load_config()
    p = Path(getattr(cfg, "profile_dir", Path.home() / ".linkedin-mcp" / "profile"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _gate():
    """Build a SafetyGate from the active profile."""
    from linkedin_mcp.ban_safety import build_default_gate
    return build_default_gate(_profile_dir())


# ---------------------------------------------------------------------------
# Safety / config tools
# ---------------------------------------------------------------------------


def get_safety_status() -> dict[str, Any]:
    """Return the current safety gate status: config + usage + pause state."""
    gate = _gate()
    return {
        "config": gate.config.to_dict(),
        "used_today": {
            "like": gate.tracker.used_today("like"),
            "comment": gate.tracker.used_today("comment"),
            "connect": gate.tracker.used_today("connect"),
            "feed_watch": gate.tracker.used_today("feed_watch"),
        },
        "used_this_hour": {
            "like": gate.tracker.used_this_hour("like"),
            "comment": gate.tracker.used_this_hour("comment"),
            "connect": gate.tracker.used_this_hour("connect"),
        },
        "paused": gate.tracker.is_paused(),
        "cooldowns": {
            at: gate.tracker.get_cooldown(at).isoformat()
            for at in ("like", "comment", "connect", "feed_watch")
            if gate.tracker.get_cooldown(at)
        },
    }


def set_safety_config(**kwargs: Any) -> dict[str, Any]:
    """Update safety config and persist to disk.

    Accepted kwargs: enabled, dry_run, tz, whitelist, blacklist,
    business_hours_start, business_hours_end, cooldown_min, cooldown_max,
    like_daily, comment_daily, connect_daily, account_age_days,
    warmup_days, warmup_multiplier, negative_response_threshold,
    shadowban_pause_hours.

    To set nested fields like business_hours, use the dotted key
    e.g. business_hours_start. For list fields (whitelist, blacklist),
    pass a list.
    """
    from linkedin_mcp.ban_safety import SafetyConfig
    pd = _profile_dir()
    cfg = SafetyConfig.load(pd / "safety_config.json")
    data = cfg.to_dict()

    # Map flat kwargs → nested data
    mapping = {
        "enabled": ("enabled",),
        "dry_run": ("dry_run",),
        "tz": ("tz",),
        "whitelist": ("whitelist",),
        "blacklist": ("blacklist",),
        "business_hours_start": ("business_hours", "start"),
        "business_hours_end": ("business_hours", "end"),
        "cooldown_min": ("cooldown_seconds", "min"),
        "cooldown_max": ("cooldown_seconds", "max"),
        "like_daily": ("daily_limits", "like"),
        "comment_daily": ("daily_limits", "comment"),
        "connect_daily": ("daily_limits", "connect"),
        "feed_watch_daily": ("daily_limits", "feed_watch"),
        "account_age_days": ("daily_limits", "account_age_days_required"),
        "warmup_days": ("daily_limits", "warmup_days"),
        "warmup_multiplier": ("daily_limits", "warmup_multiplier"),
        "like_hourly": ("hourly_limits", "like"),
        "comment_hourly": ("hourly_limits", "comment"),
        "connect_hourly": ("hourly_limits", "connect"),
        "negative_response_threshold": ("negative_response_threshold",),
        "shadowban_pause_hours": ("shadowban_alert_pause_hours",),
    }
    for k, v in kwargs.items():
        if k not in mapping:
            continue
        d = data
        for key in mapping[k][:-1]:
            d = d[key]
        d[mapping[k][-1]] = v

    new_cfg = SafetyConfig.from_dict(data)
    new_cfg.save(pd / "safety_config.json")
    return new_cfg.to_dict()


def clear_safety_pause() -> dict[str, Any]:
    """Manually clear any active pauses (negative feedback / shadow-ban)."""
    gate = _gate()
    gate.tracker.clear_pauses()
    return {"cleared": True, "paused": gate.tracker.is_paused()}


# ---------------------------------------------------------------------------
# Feed listener + digest
# ---------------------------------------------------------------------------


def poll_feed(max_items: int = 20) -> dict[str, Any]:
    """Run a single poll cycle against the feed.

    In production this would call a real scraper; for now we wrap the
    pipeline so the safety gate + storage are exercised end-to-end. A
    scraper can be plugged in by passing a fetch_fn via env var later.
    """
    from .feed_listener import FeedListener, FeedStore
    pd = _profile_dir()
    store = FeedStore(pd / "feed.db")
    gate = _gate()
    listener = FeedListener(store, safety_gate=gate)

    def fake_fetch(max_items: int = 50, item_type: str = "post"):
        # No real fetch in v1.1.0 — return empty list.
        # A real LinkedIn scraper can be plugged in by overriding this.
        return []

    result = listener.poll(fake_fetch, max_items=max_items)
    result["store_count"] = store.count()
    return result


def build_digest(lookback_hours: int = 24) -> dict[str, Any]:
    """Build a digest of the last N hours of feed activity."""
    from .feed_listener import DigestBuilder, FeedStore
    pd = _profile_dir()
    store = FeedStore(pd / "feed.db")
    digest = DigestBuilder(store).build(lookback_hours=lookback_hours)
    return digest.to_dict()


def get_digest_markdown(lookback_hours: int = 24) -> str:
    """Return the digest as Markdown text."""
    from .feed_listener import DigestBuilder, FeedStore
    pd = _profile_dir()
    store = FeedStore(pd / "feed.db")
    digest = DigestBuilder(store).build(lookback_hours=lookback_hours)
    return digest.to_markdown()


# ---------------------------------------------------------------------------
# Auto-like (Tier 3 — high risk, gated)
# ---------------------------------------------------------------------------


def auto_like_by_keyword(
    keyword: str, max_results: int = 10
) -> dict[str, Any]:
    """Search for posts matching `keyword` and like them through the safety gate.

    In dry-run mode (default) this returns what WOULD have been liked.
    """
    from .auto_like import AutoLike
    gate = _gate()
    al = AutoLike(gate)

    def fake_search(kw: str, n: int):
        # Real search wired in via fetch_fn once scraping is integrated.
        return []

    def fake_like(urn: str) -> bool:
        return True

    return al.run(
        keyword,
        search_fn=fake_search,
        like_fn=fake_like,
        max_results=max_results,
    ).to_dict()


# ---------------------------------------------------------------------------
# Auto-comment (Tier 3 — VERY high risk, strictest gate)
# ---------------------------------------------------------------------------


def auto_comment_by_keyword(
    keyword: str, max_results: int = 3, tone: str = "thought-leadership"
) -> dict[str, Any]:
    """Search for posts matching `keyword` and comment on them.

    This is the highest-risk Tier 3 feature. Defaults to dry-run.
    The AI draft is generated by the local drafter (if available).
    """
    from .auto_comment import AutoComment, CommentFilter
    gate = _gate()
    filter_ = CommentFilter()
    ac = AutoComment(gate, filter_)

    def fake_search(kw: str, n: int):
        return []

    def fake_draft(post_text: str) -> str:
        # In production this calls the local drafter
        return ""

    def fake_comment(urn: str, comment: str) -> bool:
        return True

    return ac.run(
        keyword,
        search_fn=fake_search,
        draft_fn=fake_draft,
        comment_fn=fake_comment,
        max_results=max_results,
    ).to_dict()


# ---------------------------------------------------------------------------
# Auto-connect (Tier 3 — VERY high risk)
# ---------------------------------------------------------------------------


def auto_connect_by_criteria(
    role: str = "",
    location: str = "",
    keywords: str = "",
    max_results: int = 10,
) -> dict[str, Any]:
    """Find people matching criteria, send connection requests through gate.

    All connection requests include a personalized note (no blanks).
    Defaults to dry-run.
    """
    from .auto_connect import AutoConnect, ConnectFilter
    gate = _gate()
    filter_ = ConnectFilter()
    ac = AutoConnect(gate, filter_)
    criteria = {
        "role": role,
        "location": location,
        "keywords": keywords,
    }

    def fake_search(c: dict, n: int):
        return []

    def fake_note(person) -> str:
        return ""

    def fake_connect(urn: str, note: str) -> bool:
        return True

    return ac.run(
        criteria,
        search_fn=fake_search,
        note_fn=fake_note,
        connect_fn=fake_connect,
        max_results=max_results,
    ).to_dict()


# ---------------------------------------------------------------------------
# Voice-to-post
# ---------------------------------------------------------------------------


def voice_to_draft(
    audio_path: str,
    language: str = "en",
    tone: str = "thought-leadership",
) -> dict[str, Any]:
    """Transcribe audio + clean + draft a post.

    Returns a draft for human review. Does NOT post automatically.
    """
    from .voice_to_post import (
        AudioTooLongError,
        FFmpegNotFoundError,
        TranscriptionError,
        VoiceToPost,
    )

    v = VoiceToPost()

    # Hook into the local drafter for the polish step
    def draft_fn(text: str) -> str:
        try:
            from .drafter import PostDrafter
            d = PostDrafter()
            return d.draft(text, tone=tone)
        except Exception as exc:
            log.warning("Drafter unavailable: %s", exc)
            return text

    v.draft_fn = draft_fn
    try:
        out = v.run(audio_path, language=language, tone=tone)
        return out.to_dict()
    except FFmpegNotFoundError as exc:
        return {"error": str(exc), "hint": "Install: sudo apt install ffmpeg"}
    except AudioTooLongError as exc:
        return {"error": str(exc), "hint": "Split into shorter clips"}
    except TranscriptionError as exc:
        return {"error": str(exc), "hint": "Install faster-whisper: pip install faster-whisper"}
    except Exception as exc:
        return {"error": f"unexpected: {exc}"}
