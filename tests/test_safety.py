"""Unit tests for the safety layer.

Covers quota enforcement, business hours, dry-run mode, captcha detection,
rate-limit backoff, and the ActionPlan audit flow.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from linkedin_mcp.config import Config, SafetyConfig, ServerConfig, StorageConfig, NotificationConfig
from linkedin_mcp.db import DB
from linkedin_mcp.safety import (
    ActionPlan,
    CaptchaDetectedError,
    DryRun,
    OutsideBusinessHoursError,
    QuotaExceededError,
    RateLimitedError,
    SafetyGuard,
    detect_captcha_in_text,
    jitter_seconds,
)


@pytest.fixture
def cfg() -> Config:
    # Use a 24/7 window so tests pass regardless of when run
    return Config(
        li_at="fake-li_at-for-tests",
        server=ServerConfig(),
        safety=SafetyConfig(
            daily_limit_connection_requests=20,
            daily_limit_posts=2,
            business_hours_start=0,
            business_hours_end=24,
            business_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            action_jitter_min_seconds=1,
            action_jitter_max_seconds=2,
            warmup_enabled=False,
        ),
        storage=StorageConfig(db_path=Path("/tmp/test-safety.db")),
        notifications=NotificationConfig(),
    )


@pytest.fixture
def db(tmp_path: Path) -> DB:
    return DB(tmp_path / "safety-test.db")


@pytest.fixture
def guard(cfg: Config, db: DB) -> SafetyGuard:
    return SafetyGuard(cfg, db)


# === detect_captcha_in_text ==================================================


def test_detect_captcha_positive() -> None:
    assert detect_captcha_in_text("Please complete a security check")
    assert detect_captcha_in_text("Captcha verification required")
    assert detect_captcha_in_text("Unusual activity detected, verify you are human")
    assert detect_captcha_in_text("checkpoint required")


def test_detect_captcha_negative() -> None:
    assert not detect_captcha_in_text("Welcome to your feed")
    assert not detect_captcha_in_text("Send a message to your connection")
    assert not detect_captcha_in_text("")


# === Dry run =================================================================


def test_dry_run_raises_and_audits(guard: SafetyGuard, db: DB) -> None:
    plan = ActionPlan("connection", "linkedin.com/in/alice", {"note": "hi"}, dry_run=True)
    with pytest.raises(DryRun) as exc_info:
        guard.enforce(plan)
    assert "would connection" in str(exc_info.value)

    # Audit should be recorded
    rows = db.get_audit(action="connection", limit=1)
    assert len(rows) == 1
    assert rows[0]["status"] == "dry_run"
    assert rows[0]["dry_run"] == 1
    # No quota consumed in dry-run
    assert db.get_quota("connection", limit=20).used == 0


# === Business hours ==========================================================


def test_blocks_outside_business_hours(guard: SafetyGuard, db: DB) -> None:
    plan = ActionPlan("connection", "linkedin.com/in/alice", {})

    # Fake "now" to be outside hours — patch _is_in_business_hours indirectly
    # by writing a custom guard with business_hours_start=0, end=0 (always outside)
    from linkedin_mcp.config import SafetyConfig
    custom = SafetyConfig(
        daily_limit_connection_requests=20,
        business_hours_start=0,
        business_hours_end=0,  # never open
        business_days=["mon"],
    )
    cfg2 = guard.cfg
    cfg2.safety = custom
    g2 = SafetyGuard(cfg2, db)
    with pytest.raises(OutsideBusinessHoursError):
        g2.enforce(plan)

    # Audit should record the block
    rows = db.get_audit(action="connection", limit=1)
    assert rows[0]["status"] == "blocked_safety"
    assert "outside_business_hours" in rows[0]["detail"]


# === Quota ===================================================================


def test_blocks_quota_exceeded(guard: SafetyGuard, db: DB) -> None:
    # Saturate quota
    for _ in range(20):
        db.increment_quota("connection")

    plan = ActionPlan("connection", "linkedin.com/in/alice", {})
    with pytest.raises(QuotaExceededError) as exc_info:
        guard.enforce(plan)
    assert "20/20" in str(exc_info.value)

    rows = db.get_audit(action="connection", limit=1)
    assert rows[0]["status"] == "blocked_safety"
    assert "quota_exceeded" in rows[0]["detail"]


def test_passes_when_under_quota(guard: SafetyGuard, db: DB) -> None:
    db.increment_quota("connection", n=5)
    plan = ActionPlan("connection", "linkedin.com/in/alice", {})
    # Should not raise (assuming business hours OK in test fixture)
    guard.enforce(plan)


# === Success recording ======================================================


def test_record_success_increments_quota(guard: SafetyGuard, db: DB) -> None:
    plan = ActionPlan("connection", "linkedin.com/in/alice", {})
    guard.record_success(plan, result={"invitation_id": "abc"})
    assert db.get_quota("connection", limit=20).used == 1
    rows = db.get_audit(action="connection", limit=1)
    assert rows[0]["status"] == "success"
    assert "abc" in rows[0]["detail"]


def test_record_failure_does_not_increment(guard: SafetyGuard, db: DB) -> None:
    plan = ActionPlan("connection", "linkedin.com/in/alice", {})
    guard.record_failure(plan, error="network blip")
    assert db.get_quota("connection", limit=20).used == 0
    rows = db.get_audit(action="connection", limit=1)
    assert rows[0]["status"] == "failed"


# === Rate limit / 429 ======================================================


def test_429_backoff_blocks_subsequent_actions(guard: SafetyGuard, db: DB) -> None:
    guard.record_429()
    plan = ActionPlan("connection", "linkedin.com/in/alice", {})
    with pytest.raises(RateLimitedError) as exc_info:
        guard.enforce(plan)
    assert exc_info.value.retry_after_seconds is not None
    assert exc_info.value.retry_after_seconds > 0


def test_writes_paused_flag(guard: SafetyGuard, db: DB) -> None:
    # Force a pause
    import time
    db.set_state("writes_paused_until", str(time.time() + 600))
    paused, remaining = guard.writes_paused()
    assert paused
    assert 590 <= remaining <= 600


def test_captcha_pauses_writes_24h(guard: SafetyGuard, db: DB) -> None:
    plan = ActionPlan("post", "self", {"text": "x"})
    guard.record_captcha(plan)
    paused, _ = guard.writes_paused()
    assert paused


# === Jitter ==================================================================


def test_jitter_in_range(cfg: Config) -> None:
    for _ in range(100):
        s = jitter_seconds(cfg)
        assert cfg.safety.action_jitter_min_seconds <= s <= cfg.safety.action_jitter_max_seconds


# === Warm-up =================================================================


def test_warmup_caps_first_weeks() -> None:
    from linkedin_mcp.config import SafetyConfig
    s = SafetyConfig(
        daily_limit_connection_requests=20,
        warmup_enabled=True,
        warmup_week_1_limit=5,
        warmup_week_2_limit=10,
        warmup_week_3_limit=15,
    )
    assert s.effective_daily_limit("connection", 0) == 5  # week 0/1
    assert s.effective_daily_limit("connection", 1) == 5
    assert s.effective_daily_limit("connection", 2) == 10
    assert s.effective_daily_limit("connection", 3) == 15
    assert s.effective_daily_limit("connection", 4) == 20  # week 4+: full
    assert s.effective_daily_limit("connection", 99) == 20


def test_warmup_min_with_full_cap() -> None:
    """If warmup cap > full cap, use full cap (defensive)."""
    from linkedin_mcp.config import SafetyConfig
    s = SafetyConfig(
        daily_limit_connection_requests=10,
        warmup_enabled=True,
        warmup_week_1_limit=50,  # unrealistic, but tests min()
    )
    # 50 capped to daily_limit (10)
    assert s.effective_daily_limit("connection", 0) == 10
