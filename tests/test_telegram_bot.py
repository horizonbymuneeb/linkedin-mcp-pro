"""Tests for the Telegram bot (v0.6.0)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from linkedin_mcp.telegram_bot import (
    ENV_ALLOWED_CHAT_IDS,
    ENV_BOT_TOKEN,
    TelegramBot,
    build_default_bot,
    is_allowed,
    parse_allowed_chat_ids,
    send_message,
    status,
)


# --- helpers --------------------------------------------------------------


def _bot(allowed: set[int] | None = None) -> TelegramBot:
    return TelegramBot(
        allowed=allowed or {123},
        post_fn=lambda text: {"ok": True, "chars": len(text)},
        draft_fn=lambda topic: f"draft: {topic}",
        list_schedules_fn=lambda: [
            {"name": "mon", "enabled": True, "cron": "0 9 * * 1", "next_run": "2026-06-22T09:00:00Z"},
            {"name": "off", "enabled": False, "time": "10:00", "next_run": None},
        ],
        deadman_status_fn=lambda: {
            "last_post_at": "2026-06-15T10:00:00Z",
            "days_since": 3.0,
            "threshold_days": 3,
            "status": "alert",
        },
        quota_fn=lambda: [
            {"action": "post", "used": 1, "limit": 2, "percent": 50.0},
            {"action": "connection", "used": 5, "limit": 20, "percent": 25.0},
        ],
        template_render_fn=lambda name, vars: f"rendered:{name}:{vars.get('topic', '')}",
    )


# --- allowlist parsing ----------------------------------------------------


def test_parse_allowed_chat_ids_basic() -> None:
    assert parse_allowed_chat_ids("1,2,3") == {1, 2, 3}


def test_parse_allowed_chat_ids_handles_whitespace_and_garbage() -> None:
    with patch("linkedin_mcp.telegram_bot.log") as _mock_log:
        out = parse_allowed_chat_ids("  10 , foo , 20 ,, 30 ")
    assert out == {10, 20, 30}


def test_parse_allowed_chat_ids_empty() -> None:
    assert parse_allowed_chat_ids("") == set()
    assert parse_allowed_chat_ids(None) == set()


def test_is_allowed_fail_closed_when_empty() -> None:
    # Empty allowlist must drop ALL messages — fail-closed.
    assert is_allowed(123, set()) is False


# --- command dispatch -----------------------------------------------------


def test_unknown_command_returns_helpful_message() -> None:
    bot = _bot()
    responses = bot.handle(123, "/banana")
    assert len(responses) == 1
    assert "Unknown" in responses[0].text


def test_non_command_text_is_guided() -> None:
    bot = _bot()
    responses = bot.handle(123, "hello there")
    assert len(responses) == 1
    assert "/help" in responses[0].text


def test_disallowed_chat_is_silently_dropped() -> None:
    bot = _bot(allowed={999})
    responses = bot.handle(123, "/start")
    assert responses == []


def test_empty_message_is_dropped() -> None:
    bot = _bot()
    assert bot.handle(123, "") == []
    assert bot.handle(123, "   ") == []


def test_start_includes_chat_id() -> None:
    bot = _bot(allowed={42})
    responses = bot.handle(42, "/start")
    assert len(responses) == 1
    assert "42" in responses[0].text
    assert "linkedin-mcp-pro" in responses[0].text


def test_help_lists_all_commands() -> None:
    bot = _bot()
    responses = bot.handle(123, "/help")
    txt = responses[0].text
    for cmd in ("/post", "/template", "/schedule", "/deadman", "/quota", "/draft"):
        assert cmd in txt, f"help should mention {cmd}"


def test_post_with_text_invokes_post_fn() -> None:
    seen: list[str] = []
    bot = TelegramBot(
        allowed={1},
        post_fn=lambda t: (seen.append(t) or {"ok": True, "chars": len(t)}),
    )
    responses = bot.handle(1, "/post hello world")
    assert len(responses) == 1
    assert "✅" in responses[0].text or "created" in responses[0].text
    assert seen == ["hello world"]


def test_post_without_text_shows_usage() -> None:
    bot = _bot()
    responses = bot.handle(123, "/post")
    assert "usage" in responses[0].text.lower()


def test_template_command_renders_and_calls_post() -> None:
    posted: list[str] = []
    bot = TelegramBot(
        allowed={1},
        post_fn=lambda t: (posted.append(t) or {"ok": True}),
        template_render_fn=lambda name, vars: f"<{name} topic={vars.get('topic', '?')}>",
    )
    responses = bot.handle(1, "/template weekly --var topic=AI")
    assert len(responses) == 1
    assert "✅" in responses[0].text
    assert posted == ["<weekly topic=AI>"]


def test_template_command_missing_template_returns_error() -> None:
    def _raise(name, vars):
        raise FileNotFoundError(f"template {name} not found")

    bot = TelegramBot(
        allowed={1},
        post_fn=lambda t: {"ok": True},
        template_render_fn=_raise,
    )
    responses = bot.handle(1, "/template missing")
    assert "❌" in responses[0].text
    assert "FileNotFoundError" in responses[0].text


def test_schedule_command_lists_rows() -> None:
    bot = _bot()
    responses = bot.handle(123, "/schedule")
    txt = responses[0].text
    assert "mon" in txt
    assert "off" in txt
    assert "🟢" in txt and "⚪" in txt


def test_schedule_command_empty_list() -> None:
    bot = TelegramBot(allowed={1}, list_schedules_fn=lambda: [])
    responses = bot.handle(1, "/schedule")
    assert "no schedules" in responses[0].text


def test_deadman_command_shows_status() -> None:
    bot = _bot()
    responses = bot.handle(123, "/deadman")
    txt = responses[0].text
    assert "alert" in txt
    assert "🔴" in txt
    assert "3.0" in txt  # days_since


def test_quota_command_shows_bars() -> None:
    bot = _bot()
    responses = bot.handle(123, "/quota")
    txt = responses[0].text
    assert "post" in txt
    assert "connection" in txt
    assert "█" in txt  # progress bar character


def test_draft_command_returns_preview() -> None:
    bot = _bot()
    responses = bot.handle(123, "/draft  AI agents for LinkedIn  ")
    txt = responses[0].text
    assert "draft: AI agents for LinkedIn" in txt


def test_draft_command_requires_topic() -> None:
    bot = _bot()
    responses = bot.handle(123, "/draft")
    assert "usage" in responses[0].text.lower()


def test_drafter_exception_surfaced_to_chat() -> None:
    def _raise(topic):
        raise RuntimeError("LLM down")

    bot = TelegramBot(allowed={1}, draft_fn=_raise)
    responses = bot.handle(1, "/draft foo")
    assert "❌" in responses[0].text
    assert "LLM down" in responses[0].text


# --- status & wiring ------------------------------------------------------


def test_status_with_no_bot_reports_env_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_BOT_TOKEN, "")
    monkeypatch.setenv(ENV_ALLOWED_CHAT_IDS, "1,2,3")
    s = status()
    assert s["running"] is False
    assert s["token_present"] is False
    assert sorted(s["allowed_chat_ids"]) == [1, 2, 3]
    assert s["allowlist_empty"] is False


def test_status_with_bot_instance_includes_allowed() -> None:
    bot = _bot(allowed={42})
    s = status(bot)
    assert s["running"] is True
    assert s["allowed_chat_ids"] == [42]


def test_build_default_bot_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_ALLOWED_CHAT_IDS, "111,222")
    bot = build_default_bot()
    assert bot.allowed == {111, 222}
    # All wiring functions default to real impls (not None)
    assert bot.post_fn is not None
    assert bot.draft_fn is not None
    assert bot.list_schedules_fn is not None
    assert bot.deadman_status_fn is not None
    assert bot.quota_fn is not None
    assert bot.template_render_fn is not None


# --- low-level HTTP send (urllib, no network) -----------------------------


def test_send_message_without_token_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_BOT_TOKEN, raising=False)
    assert send_message(chat_id=1, text="x") is False


def test_send_message_with_token_makes_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_BOT_TOKEN, "TEST_TOKEN")
    called: dict = {}

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=None):
        called["url"] = req.full_url if hasattr(req, "full_url") else req
        called["data"] = req.data
        called["method"] = req.method
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert send_message(chat_id=99, text="hello") is True
    assert "TEST_TOKEN" in called["url"]
    assert called["method"] == "POST"
    assert b"99" in called["data"]
    assert b"hello" in called["data"]


def test_send_message_swallows_urllib_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_BOT_TOKEN, "T")
    import urllib.error

    def _boom(*a, **kw):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    assert send_message(chat_id=1, text="x") is False
