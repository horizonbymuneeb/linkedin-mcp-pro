"""Tests for auto_like (uses injected search/like callables)."""

from __future__ import annotations

from pathlib import Path

import pytest

from linkedin_mcp.auto_like import AutoLike, PostTarget
from linkedin_mcp.ban_safety import (
    BusinessHours,
    CooldownRange,
    DailyLimits,
    HourlyLimits,
    SafetyConfig,
    SafetyGate,
    SafetyTracker,
)


@pytest.fixture
def gate(tmp_path: Path) -> SafetyGate:
    cfg = SafetyConfig(
        dry_run=False,
        business_hours=BusinessHours(start=0, end=24),
        cooldown_seconds=CooldownRange(min=0, max=0),
        daily_limits=DailyLimits(
            like=2, account_age_days_required=0, warmup_days=0
        ),
        hourly_limits=HourlyLimits(like=10),
        whitelist=["AI"],
        blacklist=["spam"],
    )
    return SafetyGate(cfg, SafetyTracker(tmp_path / "s.db"))


def test_like_dry_run_path(tmp_path: Path):
    cfg = SafetyConfig(
        dry_run=True,  # explicitly dry
        business_hours=BusinessHours(start=0, end=24),
        cooldown_seconds=CooldownRange(min=0, max=0),
        daily_limits=DailyLimits(like=10, account_age_days_required=0),
        whitelist=["AI"],
    )
    g = SafetyGate(cfg, SafetyTracker(tmp_path / "s.db"))
    al = AutoLike(g)
    calls: list[str] = []

    def search(kw, n):
        return [PostTarget(urn=f"u:{i}", author=f"a{i}", text="AI news") for i in range(3)]

    def like(urn):
        calls.append(urn)
        return True

    r = al.run("AI", search_fn=search, like_fn=like)
    assert r.found == 3
    assert r.dry_run == 3
    assert r.executed == 0
    assert calls == []  # never actually liked


def test_like_respects_whitelist(gate: SafetyGate):
    al = AutoLike(gate)
    posts = [
        PostTarget(urn="u:1", author="a", text="AI rocks"),
        PostTarget(urn="u:2", author="b", text="unrelated content"),
        PostTarget(urn="u:3", author="c", text="AI again"),
    ]
    r = al.run("AI", search_fn=lambda k, n: posts, like_fn=lambda u: True)
    assert r.found == 3
    assert r.denied == 1  # u:2 failed whitelist


def test_like_respects_blacklist(gate: SafetyGate):
    al = AutoLike(gate)
    posts = [
        PostTarget(urn="u:1", author="a", text="AI spam here"),
    ]
    r = al.run("AI", search_fn=lambda k, n: posts, like_fn=lambda u: True)
    assert r.denied == 1


def test_like_daily_limit(gate: SafetyGate):
    al = AutoLike(gate)
    # daily=2, 5 posts → first 2 allowed, rest denied
    posts = [
        PostTarget(urn=f"u:{i}", author=f"a{i}", text=f"AI post {i}")
        for i in range(5)
    ]
    r = al.run("AI", search_fn=lambda k, n: posts, like_fn=lambda u: True)
    assert r.executed == 2
    assert r.denied == 3
    assert any("daily limit" in res.decision.reason for res in r.results)


def test_like_records_failure(gate: SafetyGate):
    al = AutoLike(gate)
    posts = [PostTarget(urn="u:1", author="a", text="AI")]

    def like(urn):
        raise RuntimeError("network")

    r = al.run("AI", search_fn=lambda k, n: posts, like_fn=like)
    assert r.allowed == 1
    assert r.errors == 1
    assert r.results[0].executed is False
    assert "network" in r.results[0].error


def test_like_success_counted_in_used(gate: SafetyGate):
    al = AutoLike(gate)
    posts = [PostTarget(urn="u:1", author="a", text="AI")]
    r = al.run("AI", search_fn=lambda k, n: posts, like_fn=lambda u: True)
    assert r.executed == 1
    assert gate.tracker.used_today("like") == 1


def test_like_max_results(gate: SafetyGate):
    al = AutoLike(gate)
    posts = [
        PostTarget(urn=f"u:{i}", author=f"a{i}", text="AI")
        for i in range(100)
    ]

    def search(kw, n):
        return posts[:n]

    r = al.run("AI", search_fn=search, like_fn=lambda u: True, max_results=5)
    assert r.found == 5
