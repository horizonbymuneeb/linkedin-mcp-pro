"""Tests for auto_connect."""

from __future__ import annotations

from pathlib import Path

import pytest

from linkedin_mcp.auto_connect import (
    DEFAULT_BLACKLIST_TERMS,
    AutoConnect,
    ConnectFilter,
    PersonTarget,
)
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
# ConnectFilter
# ---------------------------------------------------------------------------


def _person(**kw) -> PersonTarget:
    defaults = dict(
        urn="u:1",
        name="Alice",
        headline="ML Engineer at Acme Corp building production AI agent systems",
        about=(
            "I build ML systems for production. Previously at Y and Z. "
            "Open source contributor to several agent frameworks and tools."
        ),
    )
    defaults.update(kw)
    return PersonTarget(**defaults)


def test_filter_already_connected():
    f = ConnectFilter()
    ok, reason = f.is_eligible(_person(is_already_connected=True))
    assert not ok and "connected" in reason


def test_filter_already_invited():
    f = ConnectFilter()
    ok, reason = f.is_eligible(_person(is_already_invited=True))
    assert not ok and "invited" in reason


def test_filter_short_headline():
    f = ConnectFilter(min_headline_length=5)
    ok, reason = f.is_eligible(_person(headline="abc"))
    assert not ok and "headline" in reason


def test_filter_short_about():
    f = ConnectFilter(min_about_length=50)
    ok, reason = f.is_eligible(_person(about="tiny"))
    assert not ok and "about" in reason


def test_filter_blacklist_headline():
    f = ConnectFilter()
    ok, reason = f.is_eligible(_person(headline="Senior Recruiter at Talent Inc"))
    assert not ok and "blacklist" in reason


def test_filter_blacklist_about():
    f = ConnectFilter()
    about = (
        "I run a crypto MLM agency, looking for new blood to join the team. "
        "We focus on passive income opportunities for motivated individuals."
    )
    ok, reason = f.is_eligible(_person(about=about))
    assert not ok and "blacklist" in reason


def test_filter_eligible():
    f = ConnectFilter()
    ok, reason = f.is_eligible(_person())
    assert ok, reason


def test_filter_note_too_short():
    f = ConnectFilter(min_note_length=80)
    ok, reason = f.is_safe_note("hi")
    assert not ok and "short" in reason


def test_filter_note_too_long():
    f = ConnectFilter(max_note_length=300)
    ok, reason = f.is_safe_note("x" * 400)
    assert not ok and "long" in reason


def test_filter_note_url_blocked():
    f = ConnectFilter()
    note = "I really enjoyed your post about agent design — see https://x.com for the deeper thread and discussion"
    ok, reason = f.is_safe_note(note)
    assert not ok and "URL" in reason


def test_filter_note_mention_blocked():
    f = ConnectFilter()
    note = "I appreciated @bob's thread on the topic and would love to discuss your take on agents"
    ok, reason = f.is_safe_note(note)
    assert not ok and "mention" in reason


def test_filter_note_blacklist_blocked():
    f = ConnectFilter()
    note = "Saw you work on agents. I run a crypto mlm agency, would love to chat about partnership"
    ok, reason = f.is_safe_note(note)
    assert not ok and "blacklist" in reason


def test_filter_note_generic_blocked():
    f = ConnectFilter()
    note = "I would like to add you to my professional network on LinkedIn and grow my network further"
    ok, reason = f.is_safe_note(note)
    assert not ok and "generic" in reason


def test_filter_note_safe():
    f = ConnectFilter()
    note = (
        "Saw your post on shipping production agent systems. "
        "We solved a similar observability problem at our team — would love to compare notes."
    )
    ok, reason = f.is_safe_note(note)
    assert ok, reason


def test_default_blacklist_has_recruiter():
    assert "recruiter" in DEFAULT_BLACKLIST_TERMS
    assert "agency" in DEFAULT_BLACKLIST_TERMS


# ---------------------------------------------------------------------------
# AutoConnect pipeline
# ---------------------------------------------------------------------------


@pytest.fixture
def gate(tmp_path: Path) -> SafetyGate:
    cfg = SafetyConfig(
        dry_run=False,
        business_hours=BusinessHours(start=0, end=24),
        cooldown_seconds=CooldownRange(min=0, max=0),
        daily_limits=DailyLimits(connect=2, account_age_days_required=0),
        hourly_limits=HourlyLimits(connect=10),
        whitelist=["AI"],
        blacklist=[],
    )
    return SafetyGate(cfg, SafetyTracker(tmp_path / "s.db"))


def test_connect_skips_already_connected(gate: SafetyGate):
    ac = AutoConnect(gate, ConnectFilter())
    p = _person(is_already_connected=True)
    r = ac.run(
        {"role": "ML"},
        search_fn=lambda c, n: [p],
        note_fn=lambda p: "Great work on agents. Would love to compare notes on observability patterns in production.",
        connect_fn=lambda u, n: True,
    )
    assert r.found == 1
    assert r.skipped == 1
    assert r.eligible == 0


def test_connect_skips_generic_note(gate: SafetyGate):
    ac = AutoConnect(gate, ConnectFilter())
    r = ac.run(
        {"role": "ML"},
        search_fn=lambda c, n: [_person()],
        note_fn=lambda p: "I would like to add you to my professional network on LinkedIn for mutual growth and shared learning",
        connect_fn=lambda u, n: True,
    )
    assert r.drafted == 1
    assert r.skipped == 1
    assert "generic" in r.results[0].skip_reason


def test_connect_dry_run(gate: SafetyGate):
    gate.config.dry_run = True
    ac = AutoConnect(gate, ConnectFilter())
    calls: list[tuple[str, str]] = []

    def conn(urn, note):
        calls.append((urn, note))
        return True

    r = ac.run(
        {"role": "ML"},
        search_fn=lambda c, n: [_person()],
        note_fn=lambda p: "Great work on agents. Would love to compare notes on observability patterns in production.",
        connect_fn=conn,
    )
    assert r.dry_run == 1
    assert r.executed == 0
    assert calls == []


def test_connect_daily_limit(gate: SafetyGate):
    ac = AutoConnect(gate, ConnectFilter())
    people = []
    for i in range(5):
        people.append(_person(urn=f"u:{i}"))
    r = ac.run(
        {"role": "ML"},
        search_fn=lambda c, n: people,
        note_fn=lambda p: "Great work on agents. Would love to compare notes on observability patterns in production.",
        connect_fn=lambda u, n: True,
    )
    assert r.eligible == 5
    assert r.executed == 2
    assert r.denied == 3


def test_connect_error_logged(gate: SafetyGate):
    ac = AutoConnect(gate, ConnectFilter())

    def conn(urn, note):
        raise RuntimeError("rate limited by LinkedIn")

    r = ac.run(
        {"role": "ML"},
        search_fn=lambda c, n: [_person()],
        note_fn=lambda p: "Great work on agents. Would love to compare notes on observability patterns in production.",
        connect_fn=conn,
    )
    assert r.allowed == 1
    assert r.errors == 1


def test_connect_full_pipeline(gate: SafetyGate):
    ac = AutoConnect(gate, ConnectFilter())
    sent: list[tuple[str, str]] = []

    def conn(urn, note):
        sent.append((urn, note))
        return True

    p = _person()
    r = ac.run(
        {"role": "ML", "location": "PK"},
        search_fn=lambda c, n: [p],
        note_fn=lambda p: "Great work on agents. Would love to compare notes on observability patterns in production.",
        connect_fn=conn,
    )
    assert r.executed == 1
    assert sent[0][0] == "u:1"
    assert "agent" in sent[0][1]
