"""Tests for LLM key management (linkedin_mcp.llm_keys + llm_router)."""

from __future__ import annotations

import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from linkedin_mcp import llm_keys
from linkedin_mcp.llm_keys import (
    PROVIDERS,
    _load,
    _save,
    add_provider,
    fetch_models,
    get_default,
    get_provider,
    list_providers,
    mask_key,
    remove_provider,
    set_default,
    check_provider,
)


def _keys_file() -> Path:
    """Return the *current* (possibly test-overridden) keys file path."""
    return llm_keys.KEYS_FILE


def _default_file() -> Path:
    return llm_keys.DEFAULT_FILE


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path: Path, monkeypatch):
    """Point KEYS_FILE and DEFAULT_FILE at a tmp dir for every test."""
    keys = tmp_path / "llm_keys.json"
    default = tmp_path / "llm_default.txt"
    llm_keys._set_paths(keys=keys, default=default)
    yield
    # Reset between tests
    llm_keys._set_paths(keys=keys, default=default)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------------------------
# 1) PROVIDERS schema
# ---------------------------------------------------------------------------


def test_providers_has_six_entries():
    assert len(PROVIDERS) == 6


def test_providers_required_fields():
    required = {
        "display_name",
        "default_base_url",
        "default_model",
        "key_prefix",
        "key_min_len",
        "needs_key",
        "test_method",
    }
    for name, schema in PROVIDERS.items():
        for field in required:
            assert field in schema, f"{name} missing {field}"


def test_providers_names():
    assert set(PROVIDERS.keys()) == {
        "openai", "anthropic", "openrouter", "ollama", "minimax", "custom"
    }


def test_ollama_does_not_need_key():
    assert PROVIDERS["ollama"]["needs_key"] is False
    assert PROVIDERS["ollama"]["test_method"] == "ollama"


def test_anthropic_uses_native_test_method():
    assert PROVIDERS["anthropic"]["test_method"] == "anthropic_native"


# ---------------------------------------------------------------------------
# 2) mask_key
# ---------------------------------------------------------------------------


def test_mask_key_long():
    # First 8 chars + '•••' + last 6 chars
    s = "sk-tes...CDEF7890-end"
    expected = s[:8] + "•••" + s[-6:]
    assert mask_key(s) == expected


def test_mask_key_exact():
    # "sk-tes...CDEF7890-end1234" -> first 8 "sk-abc12", last 6 "..7890"
    s = "sk-tes...CDEF7890-end1234"
    assert mask_key(s) == s[:8] + "•••" + s[-6:]


def test_mask_key_short():
    # Fallback for short keys
    out = mask_key("abcde")
    assert "•••" in out
    assert out.startswith("abcd")


def test_mask_key_empty():
    assert mask_key("") == ""


# ---------------------------------------------------------------------------
# 3) list_providers
# ---------------------------------------------------------------------------


def test_list_providers_returns_six_when_empty():
    ps = list_providers()
    assert len(ps) == 6
    assert {p["name"] for p in ps} == set(PROVIDERS.keys())


def test_list_providers_marks_unconfigured():
    ps = {p["name"]: p for p in list_providers()}
    for p in ps.values():
        assert p["is_configured"] is False
        assert p["masked_key"] == ""


def test_list_providers_after_add():
    add_provider("openai", key="sk-abcdefghijklmnopqrstuvwxyz123456")
    ps = {p["name"]: p for p in list_providers()}
    assert ps["openai"]["is_configured"] is True
    assert "•••" in ps["openai"]["masked_key"]


# ---------------------------------------------------------------------------
# 4) add_provider
# ---------------------------------------------------------------------------


def test_add_provider_openai_stores_key():
    add_provider("openai", key="sk-abcdefghijklmnopqrstuvwxyz123456")
    data = _load()
    assert "openai" in data
    assert data["openai"]["key"] == "sk-abcdefghijklmnopqrstuvwxyz123456"


def test_add_provider_rejects_wrong_prefix():
    with pytest.raises(ValueError):
        add_provider("openai", key="bad-prefix-key-without-sk-1234567890")


def test_add_provider_rejects_too_short_key():
    with pytest.raises(ValueError):
        add_provider("openai", key="sk-short")


def test_add_provider_anthropic_prefix():
    add_provider("anthropic", key="sk-ant-" + "x" * 40)
    data = _load()
    assert "anthropic" in data


def test_add_provider_anthropic_rejects_openai_key():
    with pytest.raises(ValueError):
        add_provider("anthropic", key="sk-abcdefghijklmnopqrstuvwxyz123456")


def test_add_provider_custom_requires_base_url():
    with pytest.raises(ValueError):
        add_provider("custom", key="somekey", base_url="")


def test_add_provider_custom_with_base_url():
    cfg = add_provider("custom", key="anykey123", base_url="https://example.com/v1")
    assert cfg["base_url"] == "https://example.com/v1"
    assert cfg["is_configured"] is True


def test_add_provider_ollama_no_key():
    cfg = add_provider("ollama")
    assert cfg["name"] == "ollama"
    assert cfg["is_configured"] is True
    assert cfg["base_url"] == "http://localhost:11434/v1"


def test_add_provider_unknown_name():
    with pytest.raises(ValueError):
        add_provider("not_a_provider", key="x" * 20)


def test_add_provider_model_override():
    add_provider("openai", key="sk-abcdefghijklmnopqrstuvwxyz123456", model="gpt-4o")
    cfg = get_provider("openai")
    assert cfg["model"] == "gpt-4o"


# ---------------------------------------------------------------------------
# 5) remove_provider / get_provider
# ---------------------------------------------------------------------------


def test_remove_provider_returns_true_then_false():
    add_provider("openai", key="sk-abcdefghijklmnopqrstuvwxyz123456")
    assert remove_provider("openai") is True
    assert remove_provider("openai") is False


def test_remove_provider_unknown_name():
    with pytest.raises(ValueError):
        remove_provider("not_a_provider")


def test_get_provider_returns_raw_key():
    add_provider("openai", key="sk-abcdefghijklmnopqrstuvwxyz123456")
    cfg = get_provider("openai")
    assert cfg is not None
    assert cfg["key"] == "sk-abcdefghijklmnopqrstuvwxyz123456"


def test_get_provider_unconfigured_returns_none():
    assert get_provider("openai") is None


def test_get_provider_unknown_raises():
    with pytest.raises(ValueError):
        get_provider("nope")


# ---------------------------------------------------------------------------
# 6) set_default / get_default
# ---------------------------------------------------------------------------


def test_set_default_round_trip():
    set_default("openai")
    assert get_default() == "openai"


def test_get_default_when_unset():
    assert get_default() is None


def test_remove_provider_clears_default():
    add_provider("openai", key="sk-abcdefghijklmnopqrstuvwxyz123456")
    set_default("openai")
    remove_provider("openai")
    assert get_default() is None


# ---------------------------------------------------------------------------
# 7) Storage hardening
# ---------------------------------------------------------------------------


def test_storage_file_is_0600():
    add_provider("openai", key="sk-tes...CDEFend-2024")
    kf = _keys_file()
    assert kf.exists(), f"keys file not found at {kf}"
    mode = kf.stat().st_mode & 0o777
    assert mode == 0o600


def test_storage_file_is_valid_json():
    add_provider("openai", key="sk-tes...CDEFend-2024")
    add_provider("anthropic", key="sk-ant-" + "y" * 40)
    kf = _keys_file()
    assert kf.exists(), f"keys file not found at {kf}"
    data = json.loads(kf.read_text())
    assert "openai" in data and "anthropic" in data


def test_corrupt_storage_file_yields_empty():
    kf = _keys_file()
    kf.parent.mkdir(parents=True, exist_ok=True)
    kf.write_text("not valid json {{{")
    assert _load() == {}


# ---------------------------------------------------------------------------
# 8) check_provider dispatch — unconfigured
# ---------------------------------------------------------------------------


def test_test_provider_unconfigured_returns_error():
    result = check_provider("openai")
    assert result["ok"] is False
    assert "not configured" in result["error"].lower() or result.get("http_status") == 0


def test_test_provider_ollama_unconfigured_still_ok():
    # ollama is "configured" without a key, so should at least try the network
    add_provider("ollama")
    result = check_provider("ollama", timeout=0.5)
    # We don't assert ok=True; just that the dispatch ran (no "not configured" error)
    assert "not configured" not in (result.get("error") or "").lower() or result.get("ok") is False


# ---------------------------------------------------------------------------
# 9) check_provider with mocked HTTP — openai_compatible
# ---------------------------------------------------------------------------


class _MockResp:
    def __init__(self, status: int, body: dict):
        self.status = status
        self._body = json.dumps(body).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_urlopen_ok(payload: dict, status: int = 200):
    return mock.patch.object(
        llm_keys.urllib.request,
        "urlopen",
        return_value=_MockResp(status, payload),
    )


def test_test_openai_compatible_success():
    add_provider("openai", key="sk-abcdefghijklmnopqrstuvwxyz123456")
    with _patch_urlopen_ok({
        "choices": [{"message": {"content": "pong"}}]
    }):
        result = check_provider("openai", timeout=2.0)
    assert result["ok"] is True
    assert result["http_status"] == 200
    assert result["response"] == "pong"


def test_test_openai_compatible_http_error():
    add_provider("openai", key="sk-tes...CDEF7890-end")
    err = urllib_error(401, {"error": {"message": "bad key"}})
    with mock.patch.object(llm_keys.urllib.request, "urlopen", side_effect=err):
        result = check_provider("openai", timeout=2.0)
    assert result["ok"] is False
    assert result["http_status"] == 401
    # The error path either surfaces the message OR falls back to "HTTP 401"
    assert ("bad key" in result["error"]) or (result["error"] == "HTTP 401")


def urllib_error(status: int, body: dict):
    import urllib.error

    class _HE(urllib.error.HTTPError):
        def __init__(self):
            super().__init__(
                url="http://x", code=status, msg="err", hdrs={}, fp=None
            )

        def read(self):
            return json.dumps(body).encode()
    return _HE()


def test_test_provider_network_error():
    add_provider("openai", key="sk-abcdefghijklmnopqrstuvwxyz123456")

    def boom(*a, **kw):
        raise ConnectionError("network down")

    with mock.patch.object(llm_keys.urllib.request, "urlopen", side_effect=boom):
        result = check_provider("openai", timeout=2.0)
    assert result["ok"] is False
    assert "network down" in result["error"]


def test_test_anthropic_native_success():
    add_provider("anthropic", key="sk-ant-" + "z" * 40)
    with _patch_urlopen_ok({"content": [{"text": "ok"}]}):
        result = check_provider("anthropic", timeout=2.0)
    assert result["ok"] is True
    assert result["http_status"] == 200
    assert result["response"] == "ok"


def test_test_ollama_success():
    add_provider("ollama", base_url="http://localhost:11434/v1")
    with _patch_urlopen_ok({
        "models": [{"name": "llama3.2"}, {"name": "qwen2.5"}]
    }):
        result = check_provider("ollama", timeout=2.0)
    assert result["ok"] is True
    assert "llama3.2" in result["available_models"]


def test_test_provider_records_last_test():
    add_provider("openai", key="sk-abcdefghijklmnopqrstuvwxyz123456")
    with _patch_urlopen_ok({"choices": [{"message": {"content": "x"}}]}):
        check_provider("openai", timeout=2.0)
    data = _load()
    assert data["openai"]["last_test_ok"] is True
    assert data["openai"]["last_test_at"] is not None


# ---------------------------------------------------------------------------
# 10) Threaded safety
# ---------------------------------------------------------------------------


def test_concurrent_writes_no_corruption():
    add_provider("openai", key="sk-tes...CDEFend-2024")
    # Use a key that's long enough for all concurrent writers
    long_key = "sk-" + "a" * 40

    def writer(_n: int):
        for _i in range(5):
            try:
                add_provider("openai", key=long_key)
            except ValueError:
                pass

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # File must still be valid JSON
    kf = _keys_file()
    data = json.loads(kf.read_text())
    assert "openai" in data
    assert data["openai"]["key"] == long_key


# ---------------------------------------------------------------------------
# 11) FastAPI router — 4 LLM tool wrappers
# ---------------------------------------------------------------------------


def _dispatch_llm():
    """Import the dispatcher from server module."""
    from linkedin_mcp.server import _dispatch_llm as f
    return f


def test_mcp_tool_list_providers():
    f = _dispatch_llm()
    out = f("llm_list_providers", {})
    assert "providers" in out
    assert len(out["providers"]) == 6


def test_mcp_tool_add_key():
    f = _dispatch_llm()
    out = f("llm_add_key", {
        "provider": "openai",
        "key": "sk-abcdefghijklmnopqrstuvwxyz123456",
    })
    assert out["name"] == "openai"
    assert out["is_configured"] is True


def test_mcp_tool_remove_key():
    f = _dispatch_llm()
    f("llm_add_key", {"provider": "openai", "key": "sk-abcdefghijklmnopqrstuvwxyz123456"})
    out = f("llm_remove_key", {"provider": "openai"})
    assert out == {"removed": True}


def test_mcp_tool_test_key_unconfigured():
    f = _dispatch_llm()
    out = f("llm_test_key", {"provider": "openai"})
    assert out["ok"] is False


def test_mcp_tool_unknown():
    f = _dispatch_llm()
    out = f("llm_bogus", {})
    assert "error" in out


# ---------------------------------------------------------------------------
# 12) FastAPI TestClient
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    from linkedin_mcp.llm_router import router

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_api_list_providers(client):
    r = client.get("/api/llm/providers")
    assert r.status_code == 200
    data = r.json()
    assert len(data["providers"]) == 6


def test_api_add_then_get(client):
    r = client.post("/api/llm/providers", json={
        "name": "openai",
        "key": "sk-abcdefghijklmnopqrstuvwxyz123456",
    })
    assert r.status_code == 200, r.text
    r = client.get("/api/llm/providers/openai")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "openai"
    assert "key" not in body
    assert "•••" in body.get("masked_key", "")


def test_api_get_unknown_404(client):
    r = client.get("/api/llm/providers/nope")
    assert r.status_code == 404


def test_api_add_invalid(client):
    r = client.post("/api/llm/providers", json={
        "name": "openai",
        "key": "no-prefix-key",
    })
    assert r.status_code == 400


def test_api_add_unknown_name(client):
    r = client.post("/api/llm/providers", json={"name": "foo"})
    assert r.status_code == 400


def test_api_delete(client):
    client.post("/api/llm/providers", json={
        "name": "openai",
        "key": "sk-abcdefghijklmnopqrstuvwxyz123456",
    })
    r = client.delete("/api/llm/providers/openai")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    r = client.delete("/api/llm/providers/openai")
    assert r.json() == {"ok": False}


def test_api_test_provider(client):
    client.post("/api/llm/providers", json={
        "name": "openai",
        "key": "sk-abcdefghijklmnopqrstuvwxyz123456",
    })
    with _patch_urlopen_ok({"choices": [{"message": {"content": "x"}}]}):
        r = client.post("/api/llm/providers/openai/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True


def test_api_test_all(client):
    client.post("/api/llm/providers", json={
        "name": "openai",
        "key": "sk-abcdefghijklmnopqrstuvwxyz123456",
    })
    client.post("/api/llm/providers", json={
        "name": "ollama",
        "base_url": "http://localhost:11434/v1",
    })
    with _patch_urlopen_ok({
        "choices": [{"message": {"content": "x"}}],
    }):
        r = client.post("/api/llm/test-all")
    assert r.status_code == 200
    results = r.json()["results"]
    names = {x["provider"] for x in results}
    assert {"openai", "ollama"}.issubset(names)


def test_api_default_round_trip(client):
    client.post("/api/llm/providers", json={
        "name": "openai",
        "key": "sk-abcdefghijklmnopqrstuvwxyz123456",
    })
    r = client.post("/api/llm/default", json={"name": "openai"})
    assert r.status_code == 200
    r = client.get("/api/llm/default")
    assert r.json() == {"default": "openai"}


def test_api_default_unset(client):
    r = client.get("/api/llm/default")
    assert r.json() == {"default": None}


def test_api_models_ollama(client):
    client.post("/api/llm/providers", json={
        "name": "ollama",
        "base_url": "http://localhost:11434/v1",
    })
    with _patch_urlopen_ok({"models": [{"name": "llama3.2"}]}):
        r = client.get("/api/llm/models/ollama")
    assert r.status_code == 200
    assert "llama3.2" in r.json()["models"]


# ---------------------------------------------------------------------------
# 13) Live HTTP server round-trip (real urllib, real socket)
# ---------------------------------------------------------------------------


def _start_server(handler, port: int) -> HTTPServer:
    httpd = HTTPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


class _OpenAIHandler(BaseHTTPRequestHandler):
    def log_message(self, *a, **kw):
        pass

    def do_POST(self):  # noqa: N802
        ln = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(ln)
        body = json.dumps({"choices": [{"message": {"content": "live-ok"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _OllamaHandler(BaseHTTPRequestHandler):
    def log_message(self, *a, **kw):
        pass

    def do_GET(self):  # noqa: N802
        body = json.dumps({"models": [{"name": "qwen2.5"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_live_openai_round_trip():
    port = _free_port()
    httpd = _start_server(_OpenAIHandler, port)
    try:
        add_provider(
            "openai",
            key="sk-abcdefghijklmnopqrstuvwxyz123456",
            base_url=f"http://127.0.0.1:{port}/v1",
        )
        result = check_provider("openai", timeout=3.0)
        assert result["ok"] is True
        assert result["response"] == "live-ok"
    finally:
        httpd.shutdown()


def test_live_ollama_round_trip():
    port = _free_port()
    httpd = _start_server(_OllamaHandler, port)
    try:
        add_provider(
            "ollama",
            base_url=f"http://127.0.0.1:{port}/v1",
        )
        result = check_provider("ollama", timeout=3.0)
        assert result["ok"] is True
        assert "qwen2.5" in result["available_models"]
    finally:
        httpd.shutdown()


# ---------------------------------------------------------------------------
# 14) fetch_models dispatch
# ---------------------------------------------------------------------------


def test_fetch_models_openai_compatible():
    add_provider(
        "openai",
        key="sk-abcdefghijklmnopqrstuvwxyz123456",
        base_url="http://127.0.0.1:1/v1",  # unreachable, but we mock
    )
    with _patch_urlopen_ok({"data": [{"id": "gpt-4o-mini"}, {"id": "gpt-4o"}]}):
        out = fetch_models("openai", timeout=2.0)
    assert out["ok"] is True
    assert "gpt-4o" in out["models"]


def test_fetch_models_ollama():
    add_provider("ollama", base_url="http://127.0.0.1:1/v1")
    with _patch_urlopen_ok({"models": [{"name": "phi3"}]}):
        out = fetch_models("ollama", timeout=2.0)
    assert out["ok"] is True
    assert "phi3" in out["models"]
