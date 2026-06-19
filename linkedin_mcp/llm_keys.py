"""LLM key management for linkedin-mcp-pro.

Stores per-provider API keys, base URLs, models, and last-test metadata in a
JSON file under the user's home directory. Default provider is tracked in a
separate text file. Pure stdlib — HTTP is done with urllib.request.
"""

from __future__ import annotations

# pytest discovers any function starting with `test_` in this module. Mark it
# explicitly as a non-test module so internal helpers like `check_provider`
# don't get collected.
__test__ = False  # noqa: A001

import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Provider schema
# ---------------------------------------------------------------------------

PROVIDERS: dict[str, dict[str, Any]] = {
    "openai": {
        "display_name": "OpenAI",
        "default_base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "key_prefix": "sk-",
        "key_min_len": 20,
        "needs_key": True,
        "test_method": "openai_compatible",
    },
    "anthropic": {
        "display_name": "Anthropic",
        "default_base_url": "https://api.anthropic.com",
        "default_model": "claude-3-5-haiku-20241022",
        "key_prefix": "sk-ant-",
        "key_min_len": 30,
        "needs_key": True,
        "test_method": "anthropic_native",
    },
    "openrouter": {
        "display_name": "OpenRouter",
        "default_base_url": "https://openrouter.ai/api/v1",
        "default_model": "anthropic/claude-3-haiku",
        "key_prefix": "sk-or-",
        "key_min_len": 20,
        "needs_key": True,
        "test_method": "openai_compatible",
    },
    "ollama": {
        "display_name": "Ollama (local)",
        "default_base_url": "http://localhost:11434/v1",
        "default_model": "llama3.2",
        "key_prefix": "",
        "key_min_len": 0,
        "needs_key": False,
        "test_method": "ollama",
    },
    "minimax": {
        "display_name": "MiniMax (default)",
        "default_base_url": "http://127.0.0.1:5000/v1",
        "default_model": "minimax-pool",
        "key_prefix": "",
        "key_min_len": 0,
        "needs_key": False,
        "test_method": "openai_compatible",
    },
    "custom": {
        "display_name": "Custom OpenAI-compatible",
        "default_base_url": "",
        "default_model": "",
        "key_prefix": "",
        "key_min_len": 0,
        "needs_key": True,
        "test_method": "openai_compatible",
    },
}

# Storage locations. Tests can monkeypatch these via _set_paths().
KEYS_FILE = Path.home() / ".linkedin-mcp" / "llm_keys.json"
DEFAULT_FILE = Path.home() / ".linkedin-mcp" / "llm_default.txt"


def _set_paths(keys: Path | None = None, default: Path | None = None) -> None:
    """Override storage paths (used by tests)."""
    global KEYS_FILE, DEFAULT_FILE
    if keys is not None:
        KEYS_FILE = keys
    if default is not None:
        DEFAULT_FILE = default


def _ensure_dir() -> None:
    KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Low-level storage
# ---------------------------------------------------------------------------


def _load() -> dict:
    """Read keys file. Returns empty dict on missing/corrupt."""
    try:
        with open(KEYS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict) -> None:
    """Atomic write — tmp file + os.replace, 0600 permissions."""
    _ensure_dir()
    fd, tmp_path = tempfile.mkstemp(
        prefix=".llm_keys_", suffix=".json", dir=str(KEYS_FILE.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, KEYS_FILE)
    except Exception:
        # Best-effort cleanup of tmp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def mask_key(key: str) -> str:
    """Show first 8 + '•••' + last 6 of a key."""
    if not key:
        return ""
    if len(key) <= 14:
        return key[:4] + "•••" + key[-2:]
    return key[:8] + "•••" + key[-6:]


def list_providers() -> list[dict]:
    """List all known providers merged with stored config."""
    stored = _load()
    out: list[dict] = []
    for name, schema in PROVIDERS.items():
        cfg = stored.get(name, {}) or {}
        key = cfg.get("key", "")
        entry = {
            "name": name,
            "display_name": schema["display_name"],
            "is_configured": bool(key) if schema["needs_key"] else bool(
                cfg.get("base_url")
            ),
            "masked_key": mask_key(key) if key else "",
            "base_url": cfg.get("base_url") or schema["default_base_url"],
            "model": cfg.get("model") or schema["default_model"],
            "needs_key": schema["needs_key"],
            "last_test_ok": cfg.get("last_test_ok"),
            "last_test_at": cfg.get("last_test_at"),
            "last_test_error": cfg.get("last_test_error"),
        }
        out.append(entry)
    return out


def _validate(name: str) -> None:
    if name not in PROVIDERS:
        raise ValueError(f"unknown provider: {name}")


def add_provider(
    name: str,
    key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> dict:
    """Add or update a provider's stored config. Returns masked stored config."""
    _validate(name)
    schema = PROVIDERS[name]
    stored = _load()

    cfg = dict(stored.get(name) or {})

    # Key validation
    if key is not None and key != "":
        prefix = schema["key_prefix"]
        min_len = schema["key_min_len"]
        if prefix and not key.startswith(prefix):
            raise ValueError(
                f"{schema['display_name']} key must start with '{prefix}'"
            )
        if min_len and len(key) < min_len:
            raise ValueError(
                f"{schema['display_name']} key must be at least {min_len} chars"
            )
        cfg["key"] = key
    elif key == "":
        # Explicit empty -> clear the key
        cfg.pop("key", None)

    # base_url
    if base_url is not None:
        if name == "custom" and not base_url.strip():
            raise ValueError("custom provider requires base_url")
        cfg["base_url"] = base_url.strip()

    if name == "custom" and not (cfg.get("base_url") or schema["default_base_url"]):
        raise ValueError("custom provider requires base_url")

    # model
    if model is not None and model != "":
        cfg["model"] = model.strip()

    # Ollama / providers without key: needs at least a base_url to be considered
    # "configured"
    if name == "ollama" and not cfg.get("base_url"):
        # default it
        cfg["base_url"] = schema["default_base_url"]

    stored[name] = cfg
    _save(stored)

    # Return masked view
    return {
        "name": name,
        "display_name": schema["display_name"],
        "is_configured": bool(cfg.get("key")) if schema["needs_key"] else bool(
            cfg.get("base_url")
        ),
        "masked_key": mask_key(cfg.get("key", "")) if cfg.get("key") else "",
        "base_url": cfg.get("base_url") or schema["default_base_url"],
        "model": cfg.get("model") or schema["default_model"],
    }


def remove_provider(name: str) -> bool:
    _validate(name)
    stored = _load()
    if name in stored:
        stored.pop(name)
        _save(stored)
        # If it was the default, clear default too
        if get_default() == name:
            try:
                DEFAULT_FILE.unlink(missing_ok=True)
            except OSError:
                pass
        return True
    return False


def get_provider(name: str) -> dict | None:
    _validate(name)
    stored = _load()
    cfg = stored.get(name)
    if not cfg:
        return None
    schema = PROVIDERS[name]
    out = dict(cfg)
    out["name"] = name
    out["display_name"] = schema["display_name"]
    out["base_url"] = out.get("base_url") or schema["default_base_url"]
    out["model"] = out.get("model") or schema["default_model"]
    return out


def set_default(name: str) -> None:
    _validate(name)
    _ensure_dir()
    DEFAULT_FILE.write_text(name, encoding="utf-8")
    try:
        os.chmod(DEFAULT_FILE, 0o600)
    except OSError:
        pass


def get_default() -> str | None:
    try:
        v = DEFAULT_FILE.read_text(encoding="utf-8").strip()
        return v or None
    except FileNotFoundError:
        return None
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Provider testing
# ---------------------------------------------------------------------------


def _http_post_json(url: str, headers: dict, body: dict, timeout: float) -> tuple[int, dict, float]:
    raw = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=raw, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    req.add_header("Content-Type", "application/json")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed = (time.time() - t0) * 1000
            status = resp.status
            payload = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        elapsed = (time.time() - t0) * 1000
        payload = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        return e.code, {"raw": payload}, elapsed
    try:
        parsed = json.loads(payload) if payload else {}
    except json.JSONDecodeError:
        parsed = {"raw": payload}
    return status, parsed, elapsed


def _http_get(url: str, headers: dict, timeout: float) -> tuple[int, dict, float]:
    req = urllib.request.Request(url, method="GET")
    for k, v in headers.items():
        req.add_header(k, v)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed = (time.time() - t0) * 1000
            status = resp.status
            payload = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        elapsed = (time.time() - t0) * 1000
        payload = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        return e.code, {"raw": payload}, elapsed
    try:
        parsed = json.loads(payload) if payload else {}
    except json.JSONDecodeError:
        parsed = {"raw": payload}
    return status, parsed, elapsed


def _test_openai_compatible(
    base_url: str, key: str, model: str, timeout: float
) -> dict:
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "stream": False,
    }
    try:
        status, payload, elapsed = _http_post_json(url, headers, body, timeout)
    except Exception as e:
        return {
            "ok": False,
            "http_status": 0,
            "elapsed_ms": 0,
            "error": f"{type(e).__name__}: {e}",
        }
    ok = 200 <= status < 300
    out = {
        "ok": ok,
        "http_status": status,
        "elapsed_ms": round(elapsed, 1),
        "model": model,
    }
    if ok:
        # Extract a small response preview
        if isinstance(payload, dict):
            choices = payload.get("choices") or []
            if choices and isinstance(choices[0], dict):
                out["response"] = choices[0].get("message", {}).get("content", "")
    else:
        err = ""
        if isinstance(payload, dict):
            err = (
                payload.get("error", {}).get("message")
                if isinstance(payload.get("error"), dict)
                else str(payload.get("error", ""))
            )
        out["error"] = err or f"HTTP {status}"
    return out


def _test_anthropic_native(
    base_url: str, key: str, model: str, timeout: float
) -> dict:
    url = base_url.rstrip("/") + "/v1/messages"
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
    }
    body = {
        "model": model,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }
    try:
        status, payload, elapsed = _http_post_json(url, headers, body, timeout)
    except Exception as e:
        return {
            "ok": False,
            "http_status": 0,
            "elapsed_ms": 0,
            "error": f"{type(e).__name__}: {e}",
        }
    ok = 200 <= status < 300
    out = {
        "ok": ok,
        "http_status": status,
        "elapsed_ms": round(elapsed, 1),
        "model": model,
    }
    if ok and isinstance(payload, dict):
        content = payload.get("content") or []
        if content and isinstance(content[0], dict):
            out["response"] = content[0].get("text", "")[:64]
    else:
        err = ""
        if isinstance(payload, dict):
            err = (
                payload.get("error", {}).get("message")
                if isinstance(payload.get("error"), dict)
                else str(payload.get("error", ""))
            )
        out["error"] = err or f"HTTP {status}"
    return out


def _test_ollama(base_url: str, key: str, model: str, timeout: float) -> dict:
    # Strip /v1 suffix to hit the native Ollama API
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    url = root.rstrip("/") + "/api/tags"
    headers = {}
    try:
        status, payload, elapsed = _http_get(url, headers, timeout)
    except Exception as e:
        return {
            "ok": False,
            "http_status": 0,
            "elapsed_ms": 0,
            "error": f"{type(e).__name__}: {e}",
        }
    ok = 200 <= status < 300
    out = {
        "ok": ok,
        "http_status": status,
        "elapsed_ms": round(elapsed, 1),
        "model": model,
    }
    if ok and isinstance(payload, dict):
        models = payload.get("models") or []
        out["available_models"] = [
            m.get("name") for m in models if isinstance(m, dict) and m.get("name")
        ]
    else:
        out["error"] = f"HTTP {status}"
    return out


def check_provider(name: str, timeout: float = 8.0) -> dict:
    """Test a provider's stored config with a 1-token ping."""
    _validate(name)
    schema = PROVIDERS[name]
    cfg = get_provider(name)
    if not cfg or (schema["needs_key"] and not cfg.get("key")):
        return {
            "ok": False,
            "http_status": 0,
            "elapsed_ms": 0,
            "error": f"{schema['display_name']} is not configured",
        }
    base_url = cfg.get("base_url") or schema["default_base_url"]
    model = cfg.get("model") or schema["default_model"]
    key = cfg.get("key", "")

    method = schema["test_method"]
    if method == "openai_compatible":
        result = _test_openai_compatible(base_url, key, model, timeout)
    elif method == "anthropic_native":
        result = _test_anthropic_native(base_url, key, model, timeout)
    elif method == "ollama":
        result = _test_ollama(base_url, key, model, timeout)
    else:
        return {
            "ok": False,
            "http_status": 0,
            "elapsed_ms": 0,
            "error": f"unknown test_method: {method}",
        }

    # Persist last test result
    stored = _load()
    cfg2 = dict(stored.get(name) or {})
    cfg2["last_test_ok"] = result.get("ok")
    cfg2["last_test_at"] = time.time()
    if not result.get("ok"):
        cfg2["last_test_error"] = result.get("error", "")
    else:
        cfg2.pop("last_test_error", None)
    stored[name] = cfg2
    try:
        _save(stored)
    except OSError:
        pass
    return result


def fetch_models(name: str, timeout: float = 8.0) -> dict:
    """Return available models for a provider."""
    _validate(name)
    schema = PROVIDERS[name]
    cfg = get_provider(name)
    base_url = (cfg or {}).get("base_url") or schema["default_base_url"]
    method = schema["test_method"]

    if method == "ollama":
        root = base_url.rstrip("/")
        if root.endswith("/v1"):
            root = root[:-3]
        url = root.rstrip("/") + "/api/tags"
        try:
            status, payload, elapsed = _http_get(url, {}, timeout)
        except Exception as e:
            return {"ok": False, "error": str(e), "models": []}
        if 200 <= status < 300 and isinstance(payload, dict):
            return {
                "ok": True,
                "models": [
                    m.get("name") for m in (payload.get("models") or []) if isinstance(m, dict)
                ],
            }
        return {"ok": False, "error": f"HTTP {status}", "models": []}

    # openai_compatible: GET /models
    url = base_url.rstrip("/") + "/models"
    headers = {}
    if cfg and cfg.get("key"):
        headers["Authorization"] = f"Bearer {cfg['key']}"
    try:
        status, payload, elapsed = _http_get(url, headers, timeout)
    except Exception as e:
        return {"ok": False, "error": str(e), "models": []}
    if 200 <= status < 300 and isinstance(payload, dict):
        data = payload.get("data") or []
        return {
            "ok": True,
            "models": [m.get("id") for m in data if isinstance(m, dict) and m.get("id")],
        }
    return {"ok": False, "error": f"HTTP {status}", "models": []}
