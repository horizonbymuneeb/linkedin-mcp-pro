"""Tests for webhooks (v1.0.0)."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from linkedin_mcp.webhooks import VALID_EVENTS, WebhookError, WebhookManager


@pytest.fixture
def mgr(tmp_path: Path) -> WebhookManager:
    return WebhookManager(tmp_path / "hooks.yaml")


def test_empty(mgr: WebhookManager) -> None:
    assert mgr.list_webhooks() == []


def test_add_and_list(mgr: WebhookManager) -> None:
    mgr.add("a", "https://example.com/hook", ["post.success"])
    hooks = mgr.list_webhooks()
    assert len(hooks) == 1
    assert hooks[0].name == "a"
    assert hooks[0].events == ["post.success"]


def test_add_duplicate_raises(mgr: WebhookManager) -> None:
    mgr.add("a", "https://e.com", ["post.success"])
    with pytest.raises(WebhookError):
        mgr.add("a", "https://e.com", ["post.success"])


def test_add_requires_url(mgr: WebhookManager) -> None:
    with pytest.raises(WebhookError):
        mgr.add("a", "", ["post.success"])


def test_add_requires_name(mgr: WebhookManager) -> None:
    with pytest.raises(WebhookError):
        mgr.add("", "https://e.com", ["post.success"])


def test_add_invalid_event_raises(mgr: WebhookManager) -> None:
    with pytest.raises(WebhookError):
        mgr.add("a", "https://e.com", ["bogus.event"])


def test_valid_events_constant_includes_common() -> None:
    assert "post.success" in VALID_EVENTS
    assert "shadowban.alert" in VALID_EVENTS
    assert "deadman.alert" in VALID_EVENTS
    assert "schedule.fired" in VALID_EVENTS


def test_remove(mgr: WebhookManager) -> None:
    mgr.add("a", "https://e.com", ["post.success"])
    assert mgr.remove("a") is True
    assert mgr.remove("a") is False


def test_remove_unknown(mgr: WebhookManager) -> None:
    assert mgr.remove("ghost") is False


def test_fire_calls_subscribed(mgr: WebhookManager) -> None:
    mgr.add("a", "https://e.com", ["post.success"])
    mgr.add("b", "https://e.com/2", ["post.failed"])
    with mock.patch("linkedin_mcp.webhooks._send_one") as mock_send:
        mgr.fire("post.success", {"x": 1}, async_=False)
        # Only "a" subscribed to post.success
        assert mock_send.call_count == 1


def test_fire_skips_disabled(mgr: WebhookManager) -> None:
    wh = mgr.add("a", "https://e.com", ["post.success"], secret="")
    wh.enabled = False
    # Manually edit the YAML to disable (since add() re-writes)
    mgr._write({"webhooks": [{**wh.to_dict(), "enabled": False, "secret": ""}]})
    with mock.patch("linkedin_mcp.webhooks._send_one") as mock_send:
        mgr.fire("post.success", {}, async_=False)
        assert mock_send.call_count == 0


def test_fire_skips_unsubscribed(mgr: WebhookManager) -> None:
    mgr.add("a", "https://e.com", ["post.success"])
    with mock.patch("linkedin_mcp.webhooks._send_one") as mock_send:
        mgr.fire("deadman.alert", {}, async_=False)
        assert mock_send.call_count == 0


def test_secret_not_persisted(mgr: WebhookManager) -> None:
    mgr.add("a", "https://e.com", ["post.success"], secret="super-secret")
    raw = mgr.path.read_text()
    assert "super-secret" not in raw


def test_fire_sends_correct_payload(tmp_path: Path) -> None:
    import urllib.request as ur
    mgr = WebhookManager(tmp_path / "wh.yaml")
    mgr.add("test", "https://example.com/hook", ["post.success"])
    captured: list[bytes] = []

    class FakeResp:
        status = 200

        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake_urlopen(req, timeout=10):
        captured.append(req.data)
        return FakeResp()

    with mock.patch.object(ur, "urlopen", fake_urlopen):
        mgr.fire("post.success", {"text": "hello"}, async_=False)
    assert len(captured) == 1
    body = captured[0].decode()
    assert "post.success" in body
    assert "hello" in body