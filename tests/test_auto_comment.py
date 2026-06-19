"""Tests for auto_comment (uses injected search/draft/comment callables)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from linkedin_mcp.auto_comment import (
    DEFAULT_BLACKLIST_PHRASES,
    AutoComment,
    CommentFilter,
    CommentTarget,
)
from linkedin_mcp.auto_like import PostTarget
from linkedin_mcp.ban_safety import (
    BusinessHours,
    CooldownRange,
    DailyLimits,
    HourlyLimits,
    SafetyConfig,
    SafetyGate,
    SafetyTracker,
)

# ---------------------------------------------------------------------------
# CommentFilter
# ---------------------------------------------------------------------------


def _target(
    text: str = "Long enough post body about AI agents and how they work in production.",
    hours_old: float = 2.0,
    connection: bool = True,
) -> CommentTarget:
    ts = (datetime.now(UTC) - timedelta(hours=hours_old)).isoformat()
    return CommentTarget(
        post=PostTarget(urn="u:1", author="alice", text=text),
        posted_at=ts,
        author_is_connection=connection,
    )


def test_filter_too_fresh():
    f = CommentFilter(min_age_hours=1.0)
    ok, reason = f.is_eligible(_target(hours_old=0.5))
    assert not ok
    assert "fresh" in reason


def test_filter_too_old():
    f = CommentFilter(max_age_days=7.0)
    ok, reason = f.is_eligible(_target(hours_old=24 * 10))
    assert not ok
    assert "old" in reason


def test_filter_too_short():
    f = CommentFilter(min_post_length=50)
    ok, reason = f.is_eligible(_target(text="short"))
    assert not ok
    assert "short" in reason


def test_filter_not_connection():
    f = CommentFilter()
    ok, reason = f.is_eligible(_target(connection=False))
    assert not ok
    assert "1st-degree" in reason


def test_filter_blacklist_in_post():
    f = CommentFilter()
    text = (
        "Check out my new course on building AI agents, "
        "DM me for the early-bird discount and full curriculum"
    )
    ok, reason = f.is_eligible(_target(text=text))
    assert not ok
    assert "blacklist" in reason


def test_filter_eligible():
    f = CommentFilter()
    ok, reason = f.is_eligible(_target())
    assert ok, reason


def test_filter_draft_too_short():
    f = CommentFilter()
    ok, _reason = f.is_safe_draft("ok")
    assert not ok


def test_filter_draft_too_long():
    f = CommentFilter()
    ok, reason = f.is_safe_draft("x" * 800)
    assert not ok
    assert "long" in reason


def test_filter_draft_url_blocked():
    f = CommentFilter()
    ok, reason = f.is_safe_draft("Great point! Read more at https://example.com for context.")
    assert not ok
    assert "URL" in reason


def test_filter_draft_mention_blocked():
    f = CommentFilter()
    ok, reason = f.is_safe_draft("I agree with @alice on this point about agents")
    assert not ok
    assert "mention" in reason


def test_filter_draft_caps_blocked():
    f = CommentFilter()
    ok, reason = f.is_safe_draft("THIS IS A GREAT POINT ABOUT AI AND SHIPPING PRODUCTS")
    assert not ok
    assert "caps" in reason


def test_filter_draft_exclamations_blocked():
    f = CommentFilter()
    ok, reason = f.is_safe_draft("Wow!!!! Great point about shipping AI products to market")
    assert not ok
    assert "exclamation" in reason


def test_filter_draft_safe():
    f = CommentFilter()
    ok, reason = f.is_safe_draft("Great point about shipping AI products. We saw similar patterns at our team last quarter.")
    assert ok, reason


def test_filter_default_blacklist_includes_common_spam():
    assert "check out my" in DEFAULT_BLACKLIST_PHRASES
    assert "DM me" in DEFAULT_BLACKLIST_PHRASES


# ---------------------------------------------------------------------------
# AutoComment pipeline
# ---------------------------------------------------------------------------


@pytest.fixture
def gate(tmp_path: Path) -> SafetyGate:
    cfg = SafetyConfig(
        dry_run=False,
        business_hours=BusinessHours(start=0, end=24),
        cooldown_seconds=CooldownRange(min=0, max=0),
        daily_limits=DailyLimits(comment=2, account_age_days_required=0),
        hourly_limits=HourlyLimits(comment=10),
        whitelist=["AI"],
        blacklist=["spam"],
    )
    return SafetyGate(cfg, SafetyTracker(tmp_path / "s.db"))


def test_comment_skips_fresh_post(gate: SafetyGate):
    ac = AutoComment(gate, CommentFilter(min_age_hours=1.0))
    fresh = _target(hours_old=0.1)
    r = ac.run(
        "AI",
        search_fn=lambda k, n: [fresh],
        draft_fn=lambda t: "Great perspective on shipping AI in production.",
        comment_fn=lambda u, c: True,
    )
    assert r.found == 1
    assert r.eligible == 0
    assert r.skipped == 1
    assert r.results[0].skip_reason


def test_comment_skips_unsafe_draft(gate: SafetyGate):
    ac = AutoComment(gate, CommentFilter())
    target = _target()
    r = ac.run(
        "AI",
        search_fn=lambda k, n: [target],
        draft_fn=lambda t: "Check out my new AI course, DM me for the link to the curriculum",
        comment_fn=lambda u, c: True,
    )
    assert r.eligible == 1
    assert r.drafted == 1
    assert r.skipped == 1
    assert r.executed == 0
    assert "blacklist" in r.results[0].skip_reason


def test_comment_dry_run(gate: SafetyGate):
    gate.config.dry_run = True
    ac = AutoComment(gate, CommentFilter())
    target = _target()
    calls: list[tuple[str, str]] = []

    def comment(urn, c):
        calls.append((urn, c))
        return True

    r = ac.run(
        "AI",
        search_fn=lambda k, n: [target],
        draft_fn=lambda t: "Great perspective on shipping AI in production.",
        comment_fn=comment,
    )
    assert r.dry_run == 1
    assert r.executed == 0
    assert calls == []


def test_comment_daily_limit(gate: SafetyGate):
    ac = AutoComment(gate, CommentFilter())
    targets = [
        _target(hours_old=2.0, connection=True) for _ in range(5)
    ]
    # Each has same urn — but we don't dedup, gate handles counts
    for i, t in enumerate(targets):
        t.post.urn = f"u:{i}"
    r = ac.run(
        "AI",
        search_fn=lambda k, n: targets,
        draft_fn=lambda t: "Great perspective on shipping AI in production.",
        comment_fn=lambda u, c: True,
    )
    assert r.eligible == 5
    assert r.executed == 2
    assert r.denied == 3


def test_comment_records_error(gate: SafetyGate):
    ac = AutoComment(gate, CommentFilter())
    target = _target()

    def comment(urn, c):
        raise RuntimeError("post failed")

    r = ac.run(
        "AI",
        search_fn=lambda k, n: [target],
        draft_fn=lambda t: "Great perspective on shipping AI in production.",
        comment_fn=comment,
    )
    assert r.allowed == 1
    assert r.errors == 1
    assert r.executed == 0


def test_comment_full_pipeline(gate: SafetyGate):
    ac = AutoComment(gate, CommentFilter())
    target = _target()
    posted: list[tuple[str, str]] = []

    def comment(urn, c):
        posted.append((urn, c))
        return True

    r = ac.run(
        "AI",
        search_fn=lambda k, n: [target],
        draft_fn=lambda t: "Great perspective on shipping AI in production.",
        comment_fn=comment,
    )
    assert r.executed == 1
    assert len(posted) == 1
    assert posted[0][0] == "u:1"
