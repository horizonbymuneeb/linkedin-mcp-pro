"""Unit tests for config loading."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from linkedin_mcp.config import Config, load_config


def test_load_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    # Clear all relevant env vars
    for k in list(os.environ.keys()):
        if k.startswith(("LI_", "MCP_", "DAILY_", "BUSINESS_", "ACTION_", "WARMUP_",
                         "RATE_", "DB_", "BROWSER_", "AUDIT_", "TELEGRAM_", "ALERT_",
                         "JSESSION", "LOG_")):
            monkeypatch.delenv(k, raising=False)

    cfg = load_config()
    assert cfg.li_at is None
    assert cfg.jsessionid is None
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 8765
    assert cfg.server.transport == "stdio"
    assert cfg.safety.daily_limit_connection_requests == 20
    assert cfg.safety.daily_limit_posts == 2
    assert cfg.safety.business_hours_start == 9
    assert cfg.safety.business_hours_end == 20
    assert cfg.safety.warmup_enabled is True


def test_load_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LI_AT", "test-li-at")
    monkeypatch.setenv("JSESSIONID", "ajax:12345")
    monkeypatch.setenv("MCP_PORT", "9000")
    monkeypatch.setenv("DAILY_LIMIT_CONNECTION_REQUESTS", "50")
    monkeypatch.setenv("BUSINESS_HOURS_START", "8")
    monkeypatch.setenv("WARMUP_ENABLED", "false")
    cfg = load_config()
    assert cfg.li_at == "test-li-at"
    assert cfg.jsessionid == "ajax:12345"
    assert cfg.server.port == 9000
    assert cfg.safety.daily_limit_connection_requests == 50
    assert cfg.safety.business_hours_start == 8
    assert cfg.safety.warmup_enabled is False


def test_li_at_file_takes_precedence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    f = tmp_path / "li_at"
    f.write_text("cookie-from-file\n")
    monkeypatch.setenv("LI_AT", "from-env")
    monkeypatch.setenv("LI_AT_FILE", str(f))
    cfg = load_config()
    # Inline env wins over file (both are tried, inline first)
    assert cfg.li_at == "from-env"

    # Now only file
    monkeypatch.delenv("LI_AT", raising=False)
    cfg = load_config()
    assert cfg.li_at == "cookie-from-file"


def test_validate_missing_li_at(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in list(os.environ.keys()):
        if k.startswith("LI_"):
            monkeypatch.delenv(k, raising=False)
    cfg = load_config()
    errors = cfg.validate()
    assert any("LI_AT" in e for e in errors)


def test_validate_bad_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LI_AT", "x")
    monkeypatch.setenv("MCP_TRANSPORT", "invalid")
    cfg = load_config()
    errors = cfg.validate()
    assert any("MCP_TRANSPORT" in e for e in errors)


def test_validate_bad_business_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LI_AT", "x")
    monkeypatch.setenv("BUSINESS_HOURS_START", "20")
    monkeypatch.setenv("BUSINESS_HOURS_END", "9")  # start > end
    cfg = load_config()
    errors = cfg.validate()
    assert any("business_hours" in e for e in errors)


def test_business_days_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LI_AT", "x")
    monkeypatch.setenv("BUSINESS_DAYS", "Mon, TUE , wed")
    cfg = load_config()
    assert cfg.safety.business_days == ["mon", "tue", "wed"]
