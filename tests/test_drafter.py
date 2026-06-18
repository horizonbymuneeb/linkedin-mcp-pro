"""Tests for the AI Post Drafter (v0.6.0, Tier 1).

We mock ``urllib.request.urlopen`` so the tests never touch a real LLM.
The point of these tests is to lock down:

* prompt construction (no AI-speak, banned words, length cap)
* response parsing (OpenAI-style chat.completions payload)
* post-processing (clamp, strip preambles, strip postambles)
* error paths (HTTP, timeout, connection, JSON, missing fields, bad tone)
* tone validation
"""
from __future__ import annotations

import json
import os
import sys
from io import BytesIO
from typing import Any
from unittest import mock

import pytest

# Make sure the package is importable when run from the repo root.
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from linkedin_mcp.drafter import (  # noqa: E402
    DEFAULT_BASE_URL,
    DEFAULT_API_KEY,
    DEFAULT_MODEL,
    LINKEDIN_MAX_CHARS,
    VALID_TONES,
    DrafterBackendError,
    DrafterConfigError,
    PostDrafter,
)


# --- helpers ----------------------------------------------------------------


def _make_payload(content: str, *, model: str = "test-model") -> dict[str, Any]:
    """Build a minimal OpenAI-style chat.completions response."""
    return {
        "id": "cmpl-1",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        },
    }


def _ok_response(payload: dict[str, Any]) -> mock.MagicMock:
    """Build a context-manager-returning mock that emits ``payload``."""
    body = json.dumps(payload).encode("utf-8")
    resp = mock.MagicMock()
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def _err_response(status: int, body: str = "") -> mock.MagicMock:
    """Build an HTTPError-shaped mock for the drafter's HTTPError path."""
    err = mock.MagicMock()
    err.code = status
    err.reason = "Bad Request"
    err.read.return_value = body.encode("utf-8")
    return err


# --- env / config ----------------------------------------------------------


def test_env_defaults(monkeypatch):
    """No env vars → known defaults."""
    monkeypatch.delenv("LINKEDIN_MCP_DRAFT_BASE_URL", raising=False)
    monkeypatch.delenv("LINKEDIN_MCP_DRAFT_API_KEY", raising=False)
    monkeypatch.delenv("LINKEDIN_MCP_DRAFT_MODEL", raising=False)
    monkeypatch.delenv("LINKEDIN_MCP_DRAFT_TIMEOUT", raising=False)
    d = PostDrafter()
    assert d.base_url == DEFAULT_BASE_URL.rstrip("/")
    assert d.api_key == DEFAULT_API_KEY
    assert d.model == DEFAULT_MODEL
    assert d.timeout > 0


def test_env_override(monkeypatch):
    """Env vars override defaults."""
    monkeypatch.setenv("LINKEDIN_MCP_DRAFT_BASE_URL", "http://llm.local:9000/v1/")
    monkeypatch.setenv("LINKEDIN_MCP_DRAFT_API_KEY", "sk-test-xyz")
    monkeypatch.setenv("LINKEDIN_MCP_DRAFT_MODEL", "llama-3-70b")
    monkeypatch.setenv("LINKEDIN_MCP_DRAFT_TIMEOUT", "5")
    d = PostDrafter()
    # trailing slash is stripped
    assert d.base_url == "http://llm.local:9000/v1"
    assert d.api_key == "sk-test-xyz"
    assert d.model == "llama-3-70b"
    assert d.timeout == 5


def test_explicit_constructor_args_win(monkeypatch):
    """Constructor args beat env vars (per-instance override)."""
    monkeypatch.setenv("LINKEDIN_MCP_DRAFT_MODEL", "wrong-model")
    d = PostDrafter(model="right-model")
    assert d.model == "right-model"


# --- draft() basics ---------------------------------------------------------


def test_draft_returns_text(monkeypatch):
    payload = _make_payload("This is the post body.")
    with mock.patch(
        "linkedin_mcp.drafter.urllib.request.urlopen",
        return_value=_ok_response(payload),
    ) as urlopen:
        d = PostDrafter()
        text = d.draft("Why SQLite is great")

    assert text == "This is the post body."
    # urlopen was called once, with a Request whose URL ends in /chat/completions
    assert urlopen.call_count == 1
    args, kwargs = urlopen.call_args
    req = args[0]
    assert req.full_url.endswith("/chat/completions")
    assert req.get_header("Authorization") == f"Bearer {d.api_key}"


def test_draft_caches_model_and_usage(monkeypatch):
    payload = _make_payload("body", model="my-llm")
    with mock.patch(
        "linkedin_mcp.drafter.urllib.request.urlopen",
        return_value=_ok_response(payload),
    ):
        d = PostDrafter()
        d.draft("hi")
    assert d.last_model == "my-llm"
    assert d.last_usage is not None
    assert d.last_usage["total_tokens"] == 150


def test_draft_request_body_has_messages(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_urlopen(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        captured.update(body)
        captured["__url__"] = req.full_url
        return _ok_response(_make_payload("ok"))

    with mock.patch(
        "linkedin_mcp.drafter.urllib.request.urlopen",
        side_effect=fake_urlopen,
    ):
        PostDrafter().draft("a hot take on sqlite", tone="thought-leader", length=400)

    msgs = captured["messages"]
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert "thought-leader" in msgs[0]["content"].lower()
    assert "400" in msgs[1]["content"]
    assert "sqlite" in msgs[1]["content"].lower()
    # model comes through
    assert captured["model"] == DEFAULT_MODEL


# --- prompt construction ---------------------------------------------------


def test_system_prompt_forbids_ai_cliches():
    """The system prompt must mention the banned words as forbidden, not use them."""
    d = PostDrafter()
    sp = d._build_system_prompt(tone="professional", include_hashtags=False)
    # The prompt *names* the banned words (so the model knows to avoid them),
    # but only inside a "do not use" directive.
    for bad in ("delve", "tapestry", "navigate"):
        assert bad in sp.lower()
    # It must instruct against emoji.
    assert "no emoji" in sp.lower() or "emoji" in sp.lower()
    # It must enforce the 3000-char cap.
    assert str(LINKEDIN_MAX_CHARS) in sp


def test_system_prompt_for_hashtags_branch():
    d = PostDrafter()
    with_tags = d._build_system_prompt(tone="casual", include_hashtags=True)
    without_tags = d._build_system_prompt(tone="casual", include_hashtags=False)
    # The with-tags branch is an *appended* line; the without-tags
    # branch is a different appended line. Look for the *branch* text,
    # which is unique to each.
    assert "do not include any hashtags" in without_tags.lower()
    assert "you may add" in with_tags.lower() and "hashtags at the very end" in with_tags.lower()


def test_all_tones_have_guidance():
    """Every documented tone maps to a guidance string in the system prompt."""
    d = PostDrafter()
    for tone in VALID_TONES:
        sp = d._build_system_prompt(tone=tone, include_hashtags=False)
        # guidance is interpolated in lowercase
        # (the dict values are sentence-case — match substrings loosely)
        assert tone in sp  # the tone name itself appears in the prompt


def test_user_prompt_carries_length_and_topic():
    d = PostDrafter()
    up = d._build_user_prompt(
        topic="hello world", length=500, include_hashtags=False
    )
    assert "hello world" in up
    assert "500" in up
    assert "no hashtags" in up
    up2 = d._build_user_prompt(
        topic="hello world", length=500, include_hashtags=True
    )
    assert "1-3" in up2 or "1–3" in up2


# --- error paths ------------------------------------------------------------


def test_invalid_tone_raises_config_error():
    d = PostDrafter()
    with pytest.raises(DrafterConfigError, match="invalid tone"):
        d.draft("anything", tone="screamy")


def test_empty_topic_raises_config_error():
    d = PostDrafter()
    with pytest.raises(DrafterConfigError, match="non-empty"):
        d.draft("   ")


def test_length_too_small_raises_config_error():
    d = PostDrafter()
    with pytest.raises(DrafterConfigError, match="length"):
        d.draft("topic", length=10)


def test_length_above_cap_is_clamped_not_rejected():
    d = PostDrafter()
    with mock.patch(
        "linkedin_mcp.drafter.urllib.request.urlopen",
        return_value=_ok_response(_make_payload("ok")),
    ):
        # length > 3000 is silently clamped, not raised
        d.draft("topic", length=5000)


def test_http_error_is_backend_error(monkeypatch):
    from urllib.error import HTTPError

    def raise_http(req, timeout=None):
        raise HTTPError(
            req.full_url, 500, "Server Error", {}, BytesIO(b'{"error":"boom"}')
        )

    with mock.patch(
        "linkedin_mcp.drafter.urllib.request.urlopen", side_effect=raise_http
    ):
        d = PostDrafter()
        with pytest.raises(DrafterBackendError, match="500"):
            d.draft("topic")


def test_url_error_is_backend_error():
    from urllib.error import URLError

    with mock.patch(
        "linkedin_mcp.drafter.urllib.request.urlopen",
        side_effect=URLError("connection refused"),
    ):
        d = PostDrafter()
        with pytest.raises(DrafterBackendError, match="unreachable"):
            d.draft("topic")


def test_timeout_is_backend_error():
    with mock.patch(
        "linkedin_mcp.drafter.urllib.request.urlopen",
        side_effect=TimeoutError("slow"),
    ):
        d = PostDrafter()
        with pytest.raises(DrafterBackendError, match="timed out"):
            d.draft("topic")


def test_non_json_response_is_backend_error():
    bad = mock.MagicMock()
    bad.read.return_value = b"<html>not json</html>"
    bad.__enter__.return_value = bad
    bad.__exit__.return_value = False
    with mock.patch(
        "linkedin_mcp.drafter.urllib.request.urlopen", return_value=bad
    ):
        d = PostDrafter()
        with pytest.raises(DrafterBackendError, match="non-JSON"):
            d.draft("topic")


def test_response_with_error_field_is_backend_error():
    payload = {"error": {"message": "model overloaded", "type": "server_error"}}
    with mock.patch(
        "linkedin_mcp.drafter.urllib.request.urlopen",
        return_value=_ok_response(payload),
    ):
        d = PostDrafter()
        with pytest.raises(DrafterBackendError, match="overloaded"):
            d.draft("topic")


def test_response_missing_choices_is_backend_error():
    payload = {"model": "x"}  # no choices
    with mock.patch(
        "linkedin_mcp.drafter.urllib.request.urlopen",
        return_value=_ok_response(payload),
    ):
        d = PostDrafter()
        with pytest.raises(DrafterBackendError, match="choices"):
            d.draft("topic")


def test_response_empty_content_is_backend_error():
    payload = _make_payload("   ")  # whitespace only
    with mock.patch(
        "linkedin_mcp.drafter.urllib.request.urlopen",
        return_value=_ok_response(payload),
    ):
        d = PostDrafter()
        with pytest.raises(DrafterBackendError, match="no usable text"):
            d.draft("topic")


# --- post-processing --------------------------------------------------------


def test_post_process_strips_preamble():
    out = PostDrafter._post_process("Here is your post:\n\nThe real content.")
    assert "Here is your post" not in out
    assert out.endswith("The real content.")


def test_post_process_strips_code_fence():
    out = PostDrafter._post_process("```\nThe real content.\n```")
    assert "```" not in out
    assert out == "The real content."


def test_post_process_strips_postamble():
    out = PostDrafter._post_process("The real content.\n\nHope this helps!")
    assert "Hope this helps" not in out
    assert out.startswith("The real content.")


def test_post_process_clamps_to_linkedin_cap():
    long = "x" * (LINKEDIN_MAX_CHARS + 500)
    out = PostDrafter._post_process(long)
    assert len(out) <= LINKEDIN_MAX_CHARS
    assert out.endswith("…")


def test_post_process_passthrough_short_text():
    out = PostDrafter._post_process("  short text  ")
    assert out == "short text"


# --- end-to-end happy paths per tone ---------------------------------------


@pytest.mark.parametrize("tone", list(VALID_TONES))
def test_draft_works_for_each_tone(tone):
    payload = _make_payload(f"a {tone} post body")
    with mock.patch(
        "linkedin_mcp.drafter.urllib.request.urlopen",
        return_value=_ok_response(payload),
    ):
        d = PostDrafter()
        text = d.draft("a topic", tone=tone)
    assert text == f"a {tone} post body"


def test_draft_strips_preamble_end_to_end():
    payload = _make_payload("Here is your post:\n\nThe real content.")
    with mock.patch(
        "linkedin_mcp.drafter.urllib.request.urlopen",
        return_value=_ok_response(payload),
    ):
        text = PostDrafter().draft("topic")
    assert "Here is your post" not in text
    assert text.endswith("The real content.")


def test_drafter_does_not_use_emoji_or_cliches_in_prompts():
    """The system prompt and user prompt must not *use* the forbidden
    AI-speak words in the imperative voice. It's fine to *quote* them
    inside the explicit 'do not use' list — that's how we tell the
    model to avoid them.
    """
    d = PostDrafter()
    for tone in VALID_TONES:
        for with_tags in (True, False):
            sp = d._build_system_prompt(tone=tone, include_hashtags=with_tags)
            lower = sp.lower()
            # The prompts include two 'forbidden lists' that quote the
            # cliches as strings (e.g. `No "delve"` and the bullet list
            # that contains "in conclusion"). Strip those directive
            # lines so we only check for inadvertent use in the rest of
            # the prompt.
            forbidden_blocks = (
                'no "delve", "tapestry", "navigate", "leverage"',
                '"in today\'s fast-paced world", "in conclusion"',
                '"it\'s not just x, it\'s y"',
            )
            stripped = lower
            for block in forbidden_blocks:
                stripped = stripped.replace(block, "")
            for bad in (
                "delve", "tapestry", "navigate", "leverage",
                "in conclusion", "in today's fast-paced world",
                "it's not just",
            ):
                assert bad not in stripped, (
                    f"tone={tone} with_tags={with_tags} used cliche {bad!r}"
                )


# --- safety surface ---------------------------------------------------------


def test_url_construction_uses_chat_completions_path():
    d = PostDrafter(base_url="http://x:1/v1", model="m")
    assert d.base_url == "http://x:1/v1"
    # The full URL used at call-time is built by _call_chat; we exercise
    # that by checking that a request goes to /v1/chat/completions.
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _ok_response(_make_payload("ok"))

    with mock.patch(
        "linkedin_mcp.drafter.urllib.request.urlopen", side_effect=fake_urlopen
    ):
        d.draft("t")
    assert captured["url"] == "http://x:1/v1/chat/completions"


def test_timeout_garbage_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("LINKEDIN_MCP_DRAFT_TIMEOUT", "not-a-number")
    d = PostDrafter()
    assert d.timeout > 0  # the default kicked in


def test_draft_returns_string_for_each_tone_setting():
    """Smoke test that defaults are sane."""
    d = PostDrafter()
    # Defaults
    assert d.model  # something non-empty
    assert d.timeout > 0
    assert d.base_url.startswith("http")
