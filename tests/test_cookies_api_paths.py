"""Regression tests for cookies_api path resolution + write fallback.

These cover the bug where cookies_api.py defaulted to /etc/linkedin-mcp-pro
(which raises PermissionError on non-root systems) and leaked the full
traceback in HTTP 500 responses.

Fixes verified here:
  * _li_at_path() never returns a /etc/... path
  * _user_data_dir() resolves to XDG_DATA_HOME or ~/.local/share
  * _write_li_at() falls back transparently on PermissionError
  * response never includes the cookie value in error detail
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest import mock

import pytest


@pytest.fixture
def fresh_cookies(monkeypatch, tmp_path: Path):
    """Reload cookies_api with a clean env (no LI_AT_FILE override)."""
    monkeypatch.delenv("LI_AT_FILE", raising=False)
    monkeypatch.delenv("LINKEDIN_MCP_PROFILE_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    import importlib
    import linkedin_mcp.cookies_api as mod
    importlib.reload(mod)
    return mod


def test_li_at_path_never_defaults_to_etc(fresh_cookies):
    """Hard regression: default path must NOT be /etc/..."""
    p = fresh_cookies._li_at_path()
    s = str(p)
    assert not s.startswith("/etc/"), f"li_at path leaked to {s}"


def test_user_data_dir_uses_xdg_when_set(fresh_cookies):
    d = fresh_cookies._user_data_dir()
    assert str(d).startswith(str(Path(os.environ["XDG_DATA_HOME"])))


def test_user_data_dir_falls_back_to_local_share(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    import importlib
    import linkedin_mcp.cookies_api as mod
    importlib.reload(mod)
    d = mod._user_data_dir()
    assert d == tmp_path / ".local" / "share" / "linkedin-mcp-pro"


def test_write_li_at_succeeds_in_safe_location(fresh_cookies, tmp_path):
    """Happy path: writes file, chmod 0600, updates env, returns meta."""
    fake_cookie = "A" * 200  # meets min length 50
    meta = fresh_cookies._write_li_at(fake_cookie, note="test")
    assert meta["fallback_used"] is False
    written_path = Path(meta["path"])
    assert written_path.exists()
    assert written_path.read_text() == fake_cookie
    # 0600 permissions
    mode = stat.S_IMODE(written_path.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"
    # Env updated
    assert os.environ["LI_AT"] == fake_cookie


def test_write_li_at_falls_back_on_permission_error(fresh_cookies, monkeypatch):
    """If requested path raises PermissionError, fall back to XDG path."""
    from linkedin_mcp import cookies_api as mod

    # Force _li_at_path to return a non-writable location
    bad = Path("/etc/linkedin-mcp-pro-readonly-test")
    monkeypatch.setattr(mod, "_li_at_path", lambda: bad)

    fake_cookie = "B" * 200
    meta = mod._write_li_at(fake_cookie, note="fallback-test")

    assert meta["fallback_used"] is True
    assert meta["requested_path"] == str(bad)
    # Wrote to fallback location
    written_path = Path(meta["path"])
    assert written_path.exists()
    assert written_path.read_text() == fake_cookie
    # Cleanup
    written_path.unlink(missing_ok=True)


def test_write_li_at_raises_http_500_on_total_failure(fresh_cookies, monkeypatch):
    """When BOTH primary and fallback fail, raise HTTPException (no traceback)."""
    from fastapi import HTTPException
    from linkedin_mcp import cookies_api as mod

    # Force both paths to fail
    monkeypatch.setattr(mod, "_li_at_path", lambda: Path("/proc/1/no-perm"))
    monkeypatch.setattr(mod, "_user_data_dir", lambda: Path("/proc/1/no-perm-2"))

    with pytest.raises(HTTPException) as exc_info:
        mod._write_li_at("X" * 200, note="fail")
    assert exc_info.value.status_code == 500
    # Error detail MUST NOT include the cookie value
    assert "X" * 200 not in str(exc_info.value.detail)
    assert "X" * 10 not in str(exc_info.value.detail)


def test_endpoint_returns_400_for_short_cookie(fresh_cookies):
    """Sanity: existing input validation still works after the fix."""
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        fresh_cookies.api_cookies_set_li_at(
            fresh_cookies.LiAtPayload(value="too-short", note="")
        )
    assert exc.value.status_code == 400


def test_endpoint_returns_400_for_long_cookie(fresh_cookies):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        fresh_cookies.api_cookies_set_li_at(
            fresh_cookies.LiAtPayload(value="X" * 600, note="")
        )
    assert exc.value.status_code == 400
