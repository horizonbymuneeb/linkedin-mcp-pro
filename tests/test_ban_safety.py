"""Tests for safety gate infrastructure."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from linkedin_mcp.ban_safety import (
    BusinessHours,
    CooldownRange,
    DailyLimits,
    HourlyLimits,
    SafetyConfig,
    SafetyDecision,
    SafetyGate,
    SafetyTracker,
    build_default_gate,
)


@pytest.fixture
def tmp_profile(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def cfg() -> SafetyConfig:
    return SafetyConfig(
        enabled=True,
        dry_run=False,
        tz="UTC",
        business_hours=BusinessHours(start=0, end=24),  # 24h open for tests
        cooldown_seconds=CooldownRange(min=0, max=0),  # disable for tests
        daily_limits=DailyLimits(
            like=3,
            comment=2,
            connect=2,
            feed_watch=100,
            account_age_days_required=0,  # disabled for tests
            warmup_days=0,
            warmup_multiplier=0.2,
        ),
        hourly_limits=HourlyLimits(like=10, comment=10, connect=10),
        negative_response_threshold=0.5,
        shadowban_alert_pause_hours=1,
        whitelist=["AI", "Python"],
        blacklist=["recruiter", "staffing"],
    )


@pytest.fixture
def tracker(tmp_profile: Path) -> SafetyTracker:
    return SafetyTracker(tmp_profile / "safety.db")


@pytest.fixture
def gate(cfg: SafetyConfig, tracker: SafetyTracker) -> SafetyGate:
    return SafetyGate(cfg, tracker)


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------


def test_config_defaults():
    cfg = SafetyConfig()
    assert cfg.enabled is True
    assert cfg.dry_run is True
    assert cfg.tz == "Asia/Karachi"
    assert cfg.business_hours.start == 9
    assert cfg.business_hours.end == 20
    assert cfg.daily_limits.like == 30
    assert cfg.daily_limits.comment == 5
    assert cfg.daily_limits.connect == 20
    assert cfg.negative_response_threshold == 0.10


def test_config_round_trip(tmp_profile: Path):
    cfg = SafetyConfig(
        enabled=True,
        dry_run=False,
        tz="America/New_York",
        whitelist=["x"],
        blacklist=["y"],
    )
    path = tmp_profile / "safety_config.json"
    cfg.save(path)
    loaded = SafetyConfig.load(path)
    assert loaded.tz == "America/New_York"
    assert loaded.whitelist == ["x"]
    assert loaded.blacklist == ["y"]


def test_config_load_missing(tmp_profile: Path):
    cfg = SafetyConfig.load(tmp_profile / "nope.json")
    assert cfg.enabled is True
    assert cfg.dry_run is True


def test_config_load_corrupt(tmp_profile: Path):
    p = tmp_profile / "safety_config.json"
    p.write_text("{not json")
    cfg = SafetyConfig.load(p)
    assert cfg.dry_run is True  # default


def test_config_to_from_dict():
    cfg = SafetyConfig(whitelist=["a", "b"], blacklist=["x"])
    d = cfg.to_dict()
    cfg2 = SafetyConfig.from_dict(d)
    assert cfg2.whitelist == ["a", "b"]
    assert cfg2.blacklist == ["x"]
    # nested
    assert cfg2.business_hours.start == 9
    assert cfg2.daily_limits.like == 30
    assert cfg2.cooldown_seconds.min == 30


# ---------------------------------------------------------------------------
# Tracker basics
# ---------------------------------------------------------------------------


def test_tracker_schema(tracker: SafetyTracker):
    with sqlite3.connect(tracker.db_path) as conn:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"actions", "cooldowns", "pauses", "negative_responses", "account_meta"} <= tables


def test_tracker_log_and_used(tracker: SafetyTracker, tmp_profile: Path):
    tracker.log_action("like", "urn:1", True, False, "ok")
    tracker.log_action("like", "urn:2", True, False, "ok")
    tracker.log_action("like", "urn:3", False, False, "denied")
    assert tracker.used_today("like") == 2
    tracker.log_action("comment", "urn:1", True, False, "ok")
    assert tracker.used_today("like") == 2
    assert tracker.used_today("comment") == 1


def test_tracker_dry_run_excluded_from_used(tracker: SafetyTracker):
    tracker.log_action("like", "urn:1", True, True, "dry-run")
    assert tracker.used_today("like") == 0


def test_tracker_cooldown(tracker: SafetyTracker):
    assert tracker.get_cooldown("like") is None
    until = tracker.set_cooldown("like", seconds=60)
    assert until > datetime.now(UTC)
    assert tracker.get_cooldown("like") is not None
    # Far-past cooldown returns None
    tracker.set_cooldown("like", seconds=-1)
    assert tracker.get_cooldown("like") is None


def test_tracker_pause(tracker: SafetyTracker):
    assert tracker.is_paused() == (False, None)
    tracker.add_pause(hours=1, reason="test")
    paused, reason = tracker.is_paused()
    assert paused is True
    assert "test" in (reason or "")
    tracker.clear_pauses()
    assert tracker.is_paused() == (False, None)


def test_tracker_negative_response_rate(tracker: SafetyTracker):
    for i in range(10):
        tracker.log_action("like", f"urn:{i}", True, False, "ok")
    # 2 negative out of 10 = 20%
    tracker.log_negative_response("like", "urn:1", "complaint")
    tracker.log_negative_response("like", "urn:2", "complaint")
    assert tracker.negative_response_rate("like") == pytest.approx(0.2)


def test_tracker_account_meta(tracker: SafetyTracker):
    assert tracker.get_account_meta("k") is None
    tracker.set_account_meta("k", "v")
    assert tracker.get_account_meta("k") == "v"


# ---------------------------------------------------------------------------
# Gate — basic allow/deny
# ---------------------------------------------------------------------------


def test_gate_disabled(gate: SafetyGate):
    gate.config.enabled = False
    d = gate.check("like", target_text="AI Python")
    assert d.allowed is False
    assert "disabled" in d.reason


def test_gate_outside_business_hours(gate: SafetyGate, tracker: SafetyTracker):
    # Empty business window (start=end=0) makes the condition always false
    gate.config.business_hours = BusinessHours(start=0, end=0)
    d = gate.check("like", target_text="AI Python")
    assert d.allowed is False
    assert "outside business hours" in d.reason


def test_gate_blacklist(gate: SafetyGate):
    d = gate.check("like", target_text="Senior Recruiter at staffing firm")
    assert d.allowed is False
    assert "blacklist" in d.reason


def test_gate_whitelist_required(gate: SafetyGate):
    d = gate.check("like", target_text="looking for a new job")
    assert d.allowed is False
    assert "whitelist" in d.reason


def test_gate_whitelist_match(gate: SafetyGate):
    d = gate.check("like", target_text="just shipped a new AI feature in Python")
    assert d.allowed is True


def test_gate_daily_limit(gate: SafetyGate):
    d1 = gate.check("like", target_text="AI thing 1")
    d2 = gate.check("like", target_text="Python thing 2")
    d3 = gate.check("like", target_text="AI thing 3")
    d4 = gate.check("like", target_text="AI thing 4")
    assert d1.allowed
    assert d2.allowed
    assert d3.allowed
    assert not d4.allowed
    assert "daily limit" in d4.reason
    assert d4.effective_limit == 3


def test_gate_hourly_limit(gate: SafetyGate):
    # Test hourly limit independent of daily by raising daily ceiling
    gate.config.daily_limits.like = 100
    gate.config.hourly_limits.like = 2
    d1 = gate.check("like", target_text="AI one")
    d2 = gate.check("like", target_text="Python two")
    d3 = gate.check("like", target_text="AI three")
    assert d1.allowed
    assert d2.allowed
    assert not d3.allowed
    assert "hourly limit" in d3.reason


def test_gate_cooldown_blocks(gate: SafetyGate):
    gate.config.cooldown_seconds = CooldownRange(min=60, max=60)
    d1 = gate.check("like", target_text="AI")
    d2 = gate.check("like", target_text="Python")
    assert d1.allowed
    assert not d2.allowed
    assert "cooldown" in d2.reason
    assert d2.cooldown_until is not None


def test_gate_dry_run(gate: SafetyGate):
    gate.config.dry_run = True
    d = gate.check("like", target_text="AI")
    assert d.allowed is False
    assert d.dry_run is True
    assert "dry-run" in d.reason
    # And usage isn't recorded as real
    assert gate.tracker.used_today("like") == 0


def test_gate_negative_response_auto_pause(gate: SafetyGate):
    # Raise daily limit so the rate check is what triggers the pause
    gate.config.daily_limits.like = 100
    # 5 likes, 3 negative → rate 60% > 50% threshold
    for i in range(5):
        gate.tracker.log_action("like", f"u:{i}", True, False, "ok")
    gate.tracker.log_negative_response("like", "u:1", "x")
    gate.tracker.log_negative_response("like", "u:2", "x")
    gate.tracker.log_negative_response("like", "u:3", "x")
    d = gate.check("like", target_text="AI")
    assert not d.allowed
    assert "auto-paused" in d.reason or "paused" in d.reason
    paused, _ = gate.tracker.is_paused()
    assert paused


def test_gate_shadowban_pause(gate: SafetyGate):
    gate.report_shadowban(hours=1)
    d = gate.check("like", target_text="AI")
    assert not d.allowed
    assert "paused" in d.reason


# ---------------------------------------------------------------------------
# Account age + warm-up
# ---------------------------------------------------------------------------


def test_gate_account_too_new(gate: SafetyGate, tracker: SafetyTracker):
    gate.config.daily_limits.account_age_days_required = 30
    tracker.set_account_meta(
        "account_created_at",
        (datetime.now(UTC) - timedelta(days=5)).isoformat(),
    )
    d = gate.check("like", target_text="AI")
    assert not d.allowed
    assert "account age" in d.reason


def test_gate_warmup_reduces_limits(gate: SafetyGate, tracker: SafetyTracker):
    gate.config.daily_limits.like = 10
    gate.config.daily_limits.warmup_days = 14
    gate.config.daily_limits.warmup_multiplier = 0.2
    tracker.set_account_meta(
        "account_created_at",
        (datetime.now(UTC) - timedelta(days=5)).isoformat(),
    )
    # effective limit = max(1, 10*0.2) = 2
    assert gate._effective_daily_limit("like") == 2


# ---------------------------------------------------------------------------
# Decision serialization
# ---------------------------------------------------------------------------


def test_safety_decision_to_dict():
    d = SafetyDecision(allowed=True, reason="ok", action_type="like", target="u:1")
    out = d.to_dict()
    assert out["allowed"] is True
    assert out["action_type"] == "like"
    assert out["cooldown_until"] is None


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def test_build_default_gate(tmp_profile: Path):
    g = build_default_gate(tmp_profile)
    assert isinstance(g, SafetyGate)
    assert g.config.dry_run is True  # default safe
