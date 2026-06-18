"""Tests for the dead-man switch (v0.5.0).

Covers:
  - status logic (ok / warning / alert / no_posts)
  - threshold resolution (env / DB session_state / explicit ctor arg)
  - 24h alert cooldown via session_state
  - Telegram send (mocked urllib) — happy path + HTTPError + URLError
  - Telegram unconfigured: warn + audit, do not crash
  - MCP tool wrappers
  - CLI subcommand handlers
  - format_message
  - audit log integration (deadman action rows)
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from urllib import error as url_error
from urllib import request as url_request

import pytest

from linkedin_mcp import cli_deadman
from linkedin_mcp.db import DB
from linkedin_mcp.deadman import (
    ALERT_COOLDOWN_HOURS,
    DEFAULT_THRESHOLD_DAYS,
    ENV_BOT_TOKEN,
    ENV_CHAT_ID,
    ENV_THRESHOLD_DAYS,
    DeadManError,
    DeadManSwitch,
)
from linkedin_mcp.tools import deadman as dm_tools


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


FIXED_NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "deadman-test.db"


@pytest.fixture
def db(db_path: Path) -> DB:
    return DB(db_path)


@pytest.fixture
def switch(db: DB) -> DeadManSwitch:
    """Default threshold=3, clock=FIXED_NOW."""
    return DeadManSwitch(db, threshold_days=3, now=FIXED_NOW)


def _insert_post(db: DB, days_ago: float, now: datetime = FIXED_NOW, status: str = "success") -> None:
    """Insert a post audit row at a controlled created_at offset."""
    ts = (now - timedelta(days=days_ago)).isoformat(timespec="seconds")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO audit_log(action, target, status, dry_run, detail, created_at) "
            "VALUES(?,?,?,?,?,?)",
            ("post", "x", status, 0, json.dumps({"post_id": "p"}), ts),
        )


def _set_last_alert(db: DB, when: datetime) -> None:
    db.set_state("deadman.last_alert_sent_at", when.isoformat(timespec="seconds"))


# ---------------------------------------------------------------------------
# check() — status logic
# ---------------------------------------------------------------------------


def test_check_no_posts_yet(switch: DeadManSwitch) -> None:
    """With an empty audit log: status=no_posts, should_alert=False."""
    r = switch.check()
    assert r["status"] == "no_posts"
    assert r["should_alert"] is False
    assert r["last_post_at"] is None
    assert r["days_since"] is None
    assert r["threshold_days"] == 3
    assert r["alert_suppressed_reason"] == "no_posts_yet"


def test_check_within_threshold_ok(switch: DeadManSwitch, db: DB) -> None:
    """0 and 1 days ago → 'ok' (well within threshold-1=2)."""
    for d in (0, 1):
        with db.transaction() as conn:
            conn.execute("DELETE FROM audit_log")
        _insert_post(db, d)
        r = switch.check()
        assert r["status"] == "ok", f"days={d} should be ok"
        assert r["should_alert"] is False
        assert r["days_since"] == pytest.approx(float(d))


def test_check_warning(switch: DeadManSwitch, db: DB) -> None:
    """2 days ago with threshold=3 → 'warning' (2 >= 2 and 2 < 3)."""
    _insert_post(db, 2)
    r = switch.check()
    assert r["status"] == "warning"
    assert r["should_alert"] is False
    assert r["days_since"] == pytest.approx(2.0)


def test_check_alert(switch: DeadManSwitch, db: DB) -> None:
    """3+ days ago with threshold=3 → 'alert', should_alert=True."""
    for d in (3, 4, 10):
        with db.transaction() as conn:
            conn.execute("DELETE FROM audit_log")
        _insert_post(db, d)
        r = switch.check()
        assert r["status"] == "alert", f"days={d} should be alert"
        assert r["should_alert"] is True
        assert r["days_since"] == pytest.approx(float(d))


def test_threshold_configurable_via_ctor(db: DB) -> None:
    """A different ctor threshold flips the boundaries correctly."""
    sw = DeadManSwitch(db, threshold_days=7, now=FIXED_NOW)
    # 5 days ago with threshold=7 → 'ok' (5 < 6)
    _insert_post(db, 5)
    r = sw.check()
    assert r["threshold_days"] == 7
    assert r["status"] == "ok"

    # 6 days ago → 'warning' (6 >= 6 and 6 < 7)
    with db.transaction() as conn:
        conn.execute("DELETE FROM audit_log")
    _insert_post(db, 6)
    r = sw.check()
    assert r["status"] == "warning"


def test_threshold_configurable_via_session_state(db: DB) -> None:
    """`set_threshold()` persists; subsequent switches pick it up."""
    sw = DeadManSwitch(db, now=FIXED_NOW)
    sw.set_threshold(10)
    # New switch, no ctor arg → should pick up 10 from session_state.
    sw2 = DeadManSwitch(db, now=FIXED_NOW)
    _insert_post(db, 5)  # 5 days ago
    r = sw2.check()
    assert r["threshold_days"] == 10
    assert r["status"] == "ok"


def test_threshold_via_env(monkeypatch: pytest.MonkeyPatch, db: DB) -> None:
    """Env var override when no ctor arg and no session_state value."""
    # Clear any DB-stored threshold from prior fixtures/tests so the env
    # value is what wins.
    db.set_state("deadman.threshold_days", "0")  # 0 fails the >=1 check
    monkeypatch.setenv(ENV_THRESHOLD_DAYS, "5")
    sw = DeadManSwitch(db, now=FIXED_NOW)
    _insert_post(db, 4)  # 4 days, env says T=5
    r = sw.check()
    assert r["threshold_days"] == 5
    # T-1 = 4; 4 >= 4 AND 4 < 5 → 'warning'
    assert r["status"] == "warning"


def test_threshold_set_rejects_below_one(switch: DeadManSwitch) -> None:
    with pytest.raises(DeadManError):
        switch.set_threshold(0)
    with pytest.raises(DeadManError):
        switch.set_threshold(-3)


def test_threshold_clamped_to_default_on_garbage(
    monkeypatch: pytest.MonkeyPatch, db: DB
) -> None:
    monkeypatch.setenv(ENV_THRESHOLD_DAYS, "not-an-int")
    sw = DeadManSwitch(db, now=FIXED_NOW)
    assert sw.get_threshold() == DEFAULT_THRESHOLD_DAYS


# ---------------------------------------------------------------------------
# Cooldown logic
# ---------------------------------------------------------------------------


def test_alert_sent_once_per_24h(switch: DeadManSwitch, db: DB) -> None:
    """After an alert, should_alert stays False for 24h, then True again."""
    _insert_post(db, 5)  # 5 days → alert status
    # First call: should alert
    r1 = switch.check()
    assert r1["should_alert"] is True

    # Simulate an alert having been sent 1h ago — still within cooldown.
    _set_last_alert(db, FIXED_NOW - timedelta(hours=1))
    r2 = switch.check()
    assert r2["status"] == "alert"
    assert r2["should_alert"] is False
    assert r2["alert_suppressed_reason"] == "cooldown_24h"

    # Move cooldown to 25h ago → should alert again.
    _set_last_alert(db, FIXED_NOW - timedelta(hours=25))
    r3 = switch.check()
    assert r3["should_alert"] is True
    assert r3["alert_suppressed_reason"] is None


# ---------------------------------------------------------------------------
# Telegram send
# ---------------------------------------------------------------------------


def _fake_response(status: int = 200, body: bytes = b'{"ok":true}'):
    resp = mock.MagicMock()
    resp.status = status
    resp.read.return_value = body
    resp.__enter__ = lambda s: resp
    resp.__exit__ = lambda s, *a: None
    return resp


def test_send_telegram_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: 2xx → True, correct URL + JSON body + timeout."""
    sent = []

    def _fake_urlopen(req, timeout=None, **_kw):
        sent.append((req, timeout))
        return _fake_response(200, b'{"ok":true,"result":{"message_id":7}}')

    monkeypatch.setattr(url_request, "urlopen", _fake_urlopen)

    ok = DeadManSwitch._send_telegram("TKN", "CHAT", "hi *there*")
    assert ok is True
    assert len(sent) == 1
    req, timeout = sent[0]
    assert req.full_url == "https://api.telegram.org/botTKN/sendMessage"
    assert timeout == 10
    payload = json.loads(req.data.decode("utf-8"))
    assert payload == {
        "chat_id": "CHAT",
        "text": "hi *there*",
        "parse_mode": "Markdown",
        "disable_web_page_preview": "true",
    }
    assert req.headers["Content-type"] == "application/json"


def test_send_telegram_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTPError from the API → False (no crash)."""

    def _raise(req, timeout=None, **_kw):
        # HTTPError(url, code, msg, hdrs, fp=None) — fp.read() returns the body.
        err = url_error.HTTPError(req.full_url, 403, "Forbidden", {}, fp=None)
        body = b'{"ok":false,"description":"chat not found"}'
        err.fp = mock.MagicMock()
        err.fp.read.return_value = body
        raise err

    monkeypatch.setattr(url_request, "urlopen", _raise)
    ok = DeadManSwitch._send_telegram("TKN", "CHAT", "hi")
    assert ok is False


def test_send_telegram_url_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Network failure (URLError) → False, no crash."""

    def _raise(req, timeout=None, **_kw):
        raise url_error.URLError("DNS failure")

    monkeypatch.setattr(url_request, "urlopen", _raise)
    assert DeadManSwitch._send_telegram("TKN", "CHAT", "hi") is False


def test_send_telegram_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """TimeoutError → False."""

    def _raise(req, timeout=None, **_kw):
        raise TimeoutError("slow DNS")

    monkeypatch.setattr(url_request, "urlopen", _raise)
    assert DeadManSwitch._send_telegram("TKN", "CHAT", "hi") is False


def test_alert_no_telegram_config_warns(
    switch: DeadManSwitch,
    db: DB,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Missing env vars → False + audit row + warning, never a crash."""
    monkeypatch.delenv(ENV_BOT_TOKEN, raising=False)
    monkeypatch.delenv(ENV_CHAT_ID, raising=False)
    _insert_post(db, 5)  # ensure should_alert=True so the path runs

    with caplog.at_level("WARNING"):
        ok = switch.send_alert(force=True)
    assert ok is False
    assert any("Telegram not configured" in r.message for r in caplog.records)

    # An audit row was written.
    rows = db.get_audit(action="deadman", limit=10)
    statuses = {r["status"] for r in rows}
    assert "telegram_unconfigured" in statuses


def test_send_alert_persists_cooldown(
    switch: DeadManSwitch, db: DB, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real send updates session_state so the next check is suppressed."""
    monkeypatch.setenv(ENV_BOT_TOKEN, "TKN")
    monkeypatch.setenv(ENV_CHAT_ID, "CHAT")
    _insert_post(db, 5)

    monkeypatch.setattr(
        url_request,
        "urlopen",
        lambda *a, **kw: _fake_response(200, b'{"ok":true}'),
    )

    assert switch.send_alert(force=False) is True
    assert db.get_state("deadman.last_alert_sent_at") is not None
    # Now cooldown is active.
    r = switch.check()
    assert r["alert_suppressed_reason"] == "cooldown_24h"


def test_test_alert_bypasses_cooldown(
    switch: DeadManSwitch, db: DB, monkeypatch: pytest.MonkeyPatch
) -> None:
    """force=True (used by test-alert) bypasses the 24h cooldown and does
    NOT update the cooldown timestamp (so the next real alert is not
    blocked by a test send)."""
    monkeypatch.setenv(ENV_BOT_TOKEN, "TKN")
    monkeypatch.setenv(ENV_CHAT_ID, "CHAT")
    one_hour_ago = FIXED_NOW - timedelta(hours=1)
    _set_last_alert(db, one_hour_ago)  # active cooldown from a real alert

    monkeypatch.setattr(
        url_request,
        "urlopen",
        lambda *a, **kw: _fake_response(200, b'{"ok":true}'),
    )

    # force=True → still sends despite the cooldown.
    assert switch.send_alert(force=True, kind="test") is True

    # Cooldown timestamp is unchanged: still the 1h-ago value, not FIXED_NOW.
    last = db.get_state("deadman.last_alert_sent_at")
    assert last is not None
    last_dt = datetime.fromisoformat(last)
    # It should be the 1h-ago value, not the current FIXED_NOW.
    assert (last_dt - one_hour_ago).total_seconds() == 0
    # And definitely NOT close to FIXED_NOW.
    assert (FIXED_NOW - last_dt).total_seconds() > 30 * 60  # > 30 min


# ---------------------------------------------------------------------------
# format_message
# ---------------------------------------------------------------------------


def test_format_message_alert_includes_emoji_and_threshold() -> None:
    text = DeadManSwitch.format_message(
        days_since=4.5, threshold=3, last_post_at="2026-06-14T09:00:00+00:00"
    )
    assert "🚨" in text
    assert "*Days since last post:* 4.5" in text
    assert "*Threshold:* 3 days" in text
    assert "2026-06-14" in text


def test_format_message_test_kind() -> None:
    text = DeadManSwitch.format_message(
        days_since=None, threshold=7, last_post_at=None, kind="test"
    )
    assert "✅" in text
    assert "test" in text.lower()
    assert "Threshold: 7 days" in text


# ---------------------------------------------------------------------------
# Audit log integration
# ---------------------------------------------------------------------------


def test_audit_log_integration(switch: DeadManSwitch, db: DB) -> None:
    """check() doesn't audit, but send_alert() writes 'deadman' rows."""
    _insert_post(db, 5)
    # Just check() — no audit rows for 'deadman'.
    switch.check()
    rows = db.get_audit(action="deadman", limit=10)
    assert rows == []

    # Cooldown-blocked alert still audits.
    _set_last_alert(db, FIXED_NOW)
    switch.send_alert(force=False)
    rows = db.get_audit(action="deadman", limit=10)
    statuses = [r["status"] for r in rows]
    assert "cooldown" in statuses


def test_failed_posts_are_ignored(switch: DeadManSwitch, db: DB) -> None:
    """A 'failed' post does NOT count as the last successful post."""
    # Insert a failed post at "today" + a successful post 5 days ago.
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO audit_log(action, target, status, dry_run, detail, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                "post",
                "x",
                "failed",
                0,
                json.dumps({"error": "blocked"}),
                (FIXED_NOW - timedelta(hours=2)).isoformat(timespec="seconds"),
            ),
        )
    _insert_post(db, 5)
    r = switch.check()
    # Should look at the 5-day-old success, not the 2h-old failure.
    assert r["status"] == "alert"
    assert r["last_post_at"] is not None
    assert r["last_post_at"].startswith("2026-06-13")


# ---------------------------------------------------------------------------
# MCP tool wrappers
# ---------------------------------------------------------------------------


def test_mcp_deadman_status(monkeypatch: pytest.MonkeyPatch, db_path: Path, db: DB) -> None:
    _path = db_path
    """deadman_status() returns the same shape as DeadManSwitch.check()."""
    # Point the MCP tool at our temp DB by patching load_config in the
    # module where it's actually used (the tool imports it directly,
    # so patching the symbol in tools/deadman.py is what matters).
    class _FakeCfg:
        class _Storage:
            db_path = _path

        storage = _Storage()

    monkeypatch.setattr(dm_tools, "load_config", lambda: _FakeCfg)
    _insert_post(db, 5)
    out = dm_tools.deadman_status()
    assert out["status"] == "alert"
    assert out["should_alert"] is True


def test_mcp_deadman_check_and_alert_no_telegram(
    monkeypatch: pytest.MonkeyPatch, db_path: Path, db: DB
) -> None:
    _path = db_path
    """check_and_alert with no Telegram config → alert_sent=False, no crash."""
    class _FakeCfg:
        class _Storage:
            db_path = _path

        storage = _Storage()

    monkeypatch.setattr(dm_tools, "load_config", lambda: _FakeCfg)
    monkeypatch.delenv(ENV_BOT_TOKEN, raising=False)
    monkeypatch.delenv(ENV_CHAT_ID, raising=False)

    _insert_post(db, 5)
    out = dm_tools.deadman_check_and_alert()
    assert out["status"] == "alert"
    assert out["alert_sent"] is False
    assert out["alert_error"] == "telegram_send_failed"


def test_mcp_deadman_test_alert_missing_config(
    monkeypatch: pytest.MonkeyPatch, db_path: Path
) -> None:
    _path = db_path
    class _FakeCfg:
        class _Storage:
            db_path = _path

        storage = _Storage()

    monkeypatch.setattr(dm_tools, "load_config", lambda: _FakeCfg)
    monkeypatch.delenv(ENV_BOT_TOKEN, raising=False)
    monkeypatch.delenv(ENV_CHAT_ID, raising=False)

    out = dm_tools.deadman_test_alert()
    assert out["sent"] is False
    assert out["error"] == "telegram_send_failed"


# ---------------------------------------------------------------------------
# CLI subcommands
# ---------------------------------------------------------------------------


def _ns(subcommand: str, days: int | None = None) -> argparse.Namespace:
    ns = argparse.Namespace()
    ns.subcommand = subcommand
    if days is not None:
        ns.days = days
    return ns


def test_cli_set_threshold_persists(
    monkeypatch: pytest.MonkeyPatch, db_path: Path, db: DB, capsys: pytest.CaptureFixture
) -> None:
    _path = db_path
    class _FakeCfg:
        class _Storage:
            db_path = _path

        storage = _Storage()

    monkeypatch.setattr(cli_deadman, "load_config", lambda: _FakeCfg)

    rc = cli_deadman.cmd_set_threshold(_ns("set-threshold", days=5))
    assert rc == 0
    out = capsys.readouterr().out
    assert "5 day" in out
    assert db.get_state("deadman.threshold_days") == "5"


def test_cli_set_threshold_rejects_zero(
    monkeypatch: pytest.MonkeyPatch, db_path: Path
) -> None:
    _path = db_path
    class _FakeCfg:
        class _Storage:
            db_path = _path

        storage = _Storage()

    monkeypatch.setattr(cli_deadman, "load_config", lambda: _FakeCfg)
    rc = cli_deadman.cmd_set_threshold(_ns("set-threshold", days=0))
    assert rc == 1


def test_cli_status_prints_table(
    monkeypatch: pytest.MonkeyPatch,
    db_path: Path,
    db: DB,
    capsys: pytest.CaptureFixture,
) -> None:
    _path = db_path
    class _FakeCfg:
        class _Storage:
            db_path = _path

        storage = _Storage()

    monkeypatch.setattr(cli_deadman, "load_config", lambda: _FakeCfg)

    _insert_post(db, 2)
    rc = cli_deadman.cmd_status(_ns("status"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Last post:" in out
    assert "warning" in out
    assert "Days since:" in out


def test_cli_test_alert_missing_config(
    monkeypatch: pytest.MonkeyPatch, db_path: Path, capsys: pytest.CaptureFixture
) -> None:
    _path = db_path
    from linkedin_mcp import config as cfg_mod

    class _FakeCfg:
        class _Storage:
            db_path = _path

        storage = _Storage()

    monkeypatch.setattr(cfg_mod, "load_config", lambda: _FakeCfg)
    monkeypatch.delenv(ENV_BOT_TOKEN, raising=False)
    monkeypatch.delenv(ENV_CHAT_ID, raising=False)

    rc = cli_deadman.cmd_test_alert(_ns("test-alert"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "Telegram not configured" in err


def test_cli_test_alert_success(
    monkeypatch: pytest.MonkeyPatch,
    db_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    _path = db_path
    from linkedin_mcp import config as cfg_mod

    class _FakeCfg:
        class _Storage:
            db_path = _path

        storage = _Storage()

    monkeypatch.setattr(cfg_mod, "load_config", lambda: _FakeCfg)
    monkeypatch.setenv(ENV_BOT_TOKEN, "TKN")
    monkeypatch.setenv(ENV_CHAT_ID, "CHAT")
    monkeypatch.setattr(
        url_request,
        "urlopen",
        lambda *a, **kw: _fake_response(200, b'{"ok":true}'),
    )

    rc = cli_deadman.cmd_test_alert(_ns("test-alert"))
    assert rc == 0
    assert "Test alert sent" in capsys.readouterr().out


def test_cli_check_within_threshold_does_not_alert(
    monkeypatch: pytest.MonkeyPatch,
    db_path: Path,
    db: DB,
    capsys: pytest.CaptureFixture,
) -> None:
    _path = db_path
    from linkedin_mcp import config as cfg_mod

    class _FakeCfg:
        class _Storage:
            db_path = _path

        storage = _Storage()

    monkeypatch.setattr(cfg_mod, "load_config", lambda: _FakeCfg)

    _insert_post(db, 1)  # ok — no alert needed
    rc = cli_deadman.cmd_check(_ns("check"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "no alert needed" in out
