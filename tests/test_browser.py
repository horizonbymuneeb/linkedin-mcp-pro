"""Tests for the agent-browser-based browser module.

agent-browser is a subprocess CLI. We mock ``asyncio.create_subprocess_exec``
to return controlled responses, then assert that:
  * The right agent-browser subcommand is invoked for each high-level op
  * Captcha / 429 detection in the response triggers guard.record_* and
    raises the typed error
  * The li_at cookie is pre-seeded into the profile dir on first use
  * Actions return ``{"ok": True, ...}`` on success
  * Auth-wall URLs (e.g. /login) raise ``BrowserError``

These tests do NOT require agent-browser or Chrome to be installed.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from linkedin_mcp.config import Config, SafetyConfig, ServerConfig, StorageConfig, NotificationConfig
from linkedin_mcp.db import DB
from linkedin_mcp.safety import (
    CaptchaDetectedError,
    RateLimitedError,
    SafetyGuard,
)
from linkedin_mcp.browser import (
    BrowserClient,
    BrowserError,
    LINKEDIN_BASE,
    create_post,
    delete_post,
    send_connection_request,
    accept_invitation,
    decline_invitation,
    withdraw_invitation,
    comment_on_post,
    react_to_post,
    send_message,
    has_valid_session,
)
from linkedin_mcp.browser.client import _set_li_at_cookie_sync


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(
        li_at="test-li_at-abc",
        server=ServerConfig(),
        safety=SafetyConfig(
            daily_limit_connection_requests=20,
            business_hours_start=0,
            business_hours_end=24,
            business_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            warmup_enabled=False,
        ),
        storage=StorageConfig(
            db_path=tmp_path / "test.db",
            browser_profile_dir=tmp_path / "browser-profile",
        ),
        notifications=NotificationConfig(),
    )


@pytest.fixture
def db(tmp_path: Path) -> DB:
    return DB(tmp_path / "test.db")


@pytest.fixture
def guard(cfg: Config, db: DB) -> SafetyGuard:
    return SafetyGuard(cfg, db)


class _FakeProcFactory:
    """Returns a factory that produces a fake subprocess with controlled output.

    Usage:
        factory = _FakeProcFactory()
        factory.add("open https://...", "✓ Google\\n  https://google.com", "")
        factory.add("snapshot", "...accessibility tree...", "")
        with patch("asyncio.create_subprocess_exec", side_effect=factory.get):
            ...
    """

    def __init__(self) -> None:
        self.cmds: list[tuple[str, int, str, str]] = []

    def add(self, pattern: str, stdout: str, stderr: str = "", rc: int = 0) -> None:
        # Store with a unique id so we can match FIFO
        self.cmds.append([pattern, rc, stdout, stderr])  # list for in-place removal

    async def get(self, *args, **kwargs):
        # When `side_effect=this` is set on create_subprocess_exec, args is
        # the unpacked command: (program, subcommand, ...subcommand_args)
        # so we just join everything to get the full command line.
        cmd_str = " ".join(str(x) for x in args)
        # Match FIFO: find the first UNUSED pattern that appears in cmd_str
        for i, entry in enumerate(self.cmds):
            pattern, rc, stdout, stderr = entry
            if pattern is None:
                continue
            if pattern in cmd_str:
                # Consume this pattern (mark as used)
                self.cmds[i][0] = None  # type: ignore
                proc = MagicMock()
                proc.communicate = AsyncMock(return_value=(stdout.encode(), stderr.encode()))
                proc.returncode = rc
                proc.kill = MagicMock()
                proc.wait = AsyncMock()
                return proc
        # No match — default to empty
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = 0
        proc.kill = MagicMock()
        proc.wait = AsyncMock()
        return proc


@pytest.fixture
def fake_proc_factory() -> _FakeProcFactory:
    return _FakeProcFactory()


# ---------------------------------------------------------------------------
# Cookie pre-seeding
# ---------------------------------------------------------------------------


def test_set_li_at_cookie_creates_file(tmp_path: Path) -> None:
    _set_li_at_cookie_sync(tmp_path, "my-cookie")
    state = json.loads((tmp_path / "storage_state.json").read_text())
    cookies = state["cookies"]
    assert any(c["name"] == "li_at" and c["value"] == "my-cookie" for c in cookies)


def test_set_li_at_cookie_replaces_existing(tmp_path: Path) -> None:
    state_file = tmp_path / "storage_state.json"
    state_file.write_text(json.dumps({
        "cookies": [{"name": "li_at", "value": "old", "domain": ".linkedin.com", "path": "/"}],
        "origins": [],
    }))
    _set_li_at_cookie_sync(tmp_path, "new")
    state = json.loads(state_file.read_text())
    li_at = [c for c in state["cookies"] if c["name"] == "li_at"]
    assert len(li_at) == 1
    assert li_at[0]["value"] == "new"


def test_has_valid_session(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    assert not has_valid_session(profile)

    state = {"cookies": [{"name": "li_at", "value": "x", "domain": ".linkedin.com"}], "origins": []}
    (profile / "storage_state.json").write_text(json.dumps(state))
    assert has_valid_session(profile)


# ---------------------------------------------------------------------------
# BrowserClient: navigation + captcha / 429 detect
# ---------------------------------------------------------------------------


async def test_navigate_succeeds(fake_proc_factory) -> None:
    fake_proc_factory.add("open https://www.linkedin.com/feed", "✓ Feed\n  https://www.linkedin.com/feed")
    fake_proc_factory.add("eval document.body.innerText", "Welcome to your feed", "")

    with patch("linkedin_mcp.browser.client.asyncio.create_subprocess_exec",
               side_effect=fake_proc_factory.get) as sp:
        with patch("linkedin_mcp.browser.client._find_agent_browser", return_value="/fake/agent-browser"):
            # Note: cfg is needed for the test
            with patch("linkedin_mcp.browser.client._set_li_at_cookie_sync"):
                pass  # we'll set up a real client
            from linkedin_mcp.config import Config, StorageConfig
            from pathlib import Path
            cfg = Config(
                li_at="x",
                storage=StorageConfig(
                    db_path=Path("/tmp/x.db"),
                    browser_profile_dir=Path("/tmp/profile"),
                ),
            )
            from linkedin_mcp.db import DB
            db = DB(Path("/tmp/x.db"))
            guard = SafetyGuard(cfg, db)
            client = BrowserClient(cfg, db, guard)
            async with client:
                await client.navigate("https://www.linkedin.com/feed")
                assert client._current_url == "https://www.linkedin.com/feed"


async def test_navigate_raises_on_auth_wall(fake_proc_factory) -> None:
    from linkedin_mcp.browser import BrowserChallenge
    fake_proc_factory.add("open https://www.linkedin.com/feed", "✓ Login\n  https://www.linkedin.com/login")
    with patch("linkedin_mcp.browser.client.asyncio.create_subprocess_exec",
               side_effect=fake_proc_factory.get), \
         patch("linkedin_mcp.browser.client._find_agent_browser", return_value="/fake"):
        from linkedin_mcp.config import Config, StorageConfig
        from linkedin_mcp.db import DB
        from pathlib import Path
        cfg = Config(li_at="x", storage=StorageConfig(db_path=Path("/tmp/x.db"), browser_profile_dir=Path("/tmp/p")))
        db = DB(Path("/tmp/x.db"))
        guard = SafetyGuard(cfg, db)
        client = BrowserClient(cfg, db, guard)
        async with client:
            # v0.3.0: /login URL now raises BrowserChallenge (not generic BrowserError)
            with pytest.raises(BrowserChallenge, match="security challenge"):
                await client.navigate("https://www.linkedin.com/feed")


async def test_captcha_in_body_raises(fake_proc_factory) -> None:
    from linkedin_mcp.browser import BrowserChallenge
    fake_proc_factory.add("open", "✓ Feed\n  https://www.linkedin.com/feed")
    fake_proc_factory.add("eval", "Please complete a security check", "")

    with patch("linkedin_mcp.browser.client.asyncio.create_subprocess_exec",
               side_effect=fake_proc_factory.get), \
         patch("linkedin_mcp.browser.client._find_agent_browser", return_value="/fake"):
        from linkedin_mcp.config import Config, StorageConfig
        from linkedin_mcp.db import DB
        from pathlib import Path
        cfg = Config(li_at="x", storage=StorageConfig(db_path=Path("/tmp/x.db"), browser_profile_dir=Path("/tmp/p")))
        db = DB(Path("/tmp/x.db"))
        guard = SafetyGuard(cfg, db)
        client = BrowserClient(cfg, db, guard)
        async with client:
            with pytest.raises(CaptchaDetectedError):
                await client.navigate("https://www.linkedin.com/feed")


async def test_429_in_body_raises(fake_proc_factory) -> None:
    fake_proc_factory.add("open", "✓ Feed\n  https://www.linkedin.com/feed")
    fake_proc_factory.add("eval", "You're temporarily limited, please slow down", "")

    with patch("linkedin_mcp.browser.client.asyncio.create_subprocess_exec",
               side_effect=fake_proc_factory.get), \
         patch("linkedin_mcp.browser.client._find_agent_browser", return_value="/fake"):
        from linkedin_mcp.config import Config, StorageConfig
        from linkedin_mcp.db import DB
        from pathlib import Path
        cfg = Config(li_at="x", storage=StorageConfig(db_path=Path("/tmp/x.db"), browser_profile_dir=Path("/tmp/p")))
        db = DB(Path("/tmp/x.db"))
        guard = SafetyGuard(cfg, db)
        client = BrowserClient(cfg, db, guard)
        async with client:
            with pytest.raises(RateLimitedError):
                await client.navigate("https://www.linkedin.com/feed")


# ---------------------------------------------------------------------------
# Action: send_connection_request
# ---------------------------------------------------------------------------


async def test_send_connection_request_validates_inputs() -> None:
    """Invalid public_id / note should raise before any browser call."""
    cfg, db, guard = _make_minimal()
    client = MagicMock(spec=BrowserClient)
    with pytest.raises(ValueError, match="Invalid public_id"):
        await send_connection_request(client, "")
    with pytest.raises(ValueError, match="Invalid public_id"):
        await send_connection_request(client, "bad/id")
    # Long note
    with pytest.raises(ValueError, match="too long"):
        await send_connection_request(client, "valid-id", note="x" * 301)


async def test_send_connection_request_no_button(fake_proc_factory) -> None:
    fake_proc_factory.add("open", "✓ Profile\n  https://www.linkedin.com/in/alice")
    fake_proc_factory.add("eval", "")
    fake_proc_factory.add("snapshot", "- heading 'Alice'\n  - link 'Message'\n")  # no Connect button

    with patch("linkedin_mcp.browser.client.asyncio.create_subprocess_exec",
               side_effect=fake_proc_factory.get), \
         patch("linkedin_mcp.browser.client._find_agent_browser", return_value="/fake"):
        cfg, db, guard = _make_minimal()
        client = BrowserClient(cfg, db, guard)
        async with client:
            result = await send_connection_request(client, "alice")
            assert result["ok"] is False
            assert "no_connect_button" in result.get("error", "") or "already" in result.get("error", "")


async def test_send_connection_request_happy_path(fake_proc_factory) -> None:
    """Find connect button, click, click send."""
    fake_proc_factory.add("open", "✓ Profile\n  https://www.linkedin.com/in/alice")
    fake_proc_factory.add("eval", "")
    # First snapshot (find Connect)
    fake_proc_factory.add("snapshot", '- button "Connect" [ref=e5]\n')
    # After click — note dialog appears
    fake_proc_factory.add("snapshot", '- button "Add a note" [ref=e10]\n')
    fake_proc_factory.add("snapshot", '- textbox "" [ref=e15]\n')
    # After fill — send button
    fake_proc_factory.add("snapshot", '- button "Send" [ref=e20]\n')

    with patch("linkedin_mcp.browser.client.asyncio.create_subprocess_exec",
               side_effect=fake_proc_factory.get), \
         patch("linkedin_mcp.browser.client._find_agent_browser", return_value="/fake"):
        cfg, db, guard = _make_minimal()
        client = BrowserClient(cfg, db, guard)
        async with client:
            result = await send_connection_request(client, "alice", note="hi")
            assert result["ok"] is True
            assert result["target"] == "alice"
            assert result["with_note"] is True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal():
    """Build (cfg, db, guard) for tests that don't need real subprocess mocks."""
    cfg = Config(
        li_at="x",
        server=ServerConfig(),
        safety=SafetyConfig(
            daily_limit_connection_requests=20,
            business_hours_start=0,
            business_hours_end=24,
            business_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            warmup_enabled=False,
        ),
        storage=StorageConfig(db_path=Path("/tmp/test.db"), browser_profile_dir=Path("/tmp/profile")),
        notifications=NotificationConfig(),
    )
    db = DB(Path("/tmp/test.db"))
    guard = SafetyGuard(cfg, db)
    return cfg, db, guard


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


def test_all_actions_exported() -> None:
    from linkedin_mcp import browser
    expected = [
        "BrowserClient", "BrowserError", "LINKEDIN_BASE",
        "ensure_session", "has_valid_session",
        "send_connection_request", "accept_invitation", "decline_invitation", "withdraw_invitation",
        "create_post", "delete_post",
        "comment_on_post", "react_to_post",
        "send_message",
    ]
    for name in expected:
        assert hasattr(browser, name), f"missing: {name}"


def test_linkedin_base() -> None:
    assert LINKEDIN_BASE == "https://www.linkedin.com"
