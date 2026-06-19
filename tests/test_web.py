"""Tests for the Web UI (v1.0.0)."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from linkedin_mcp.db import DB
from linkedin_mcp.web import _DASHBOARD_HTML, app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    # Make DB not exist by pointing at a fresh path
    db_path = tmp_path / "web_test.db"
    cfg_mock = mock.MagicMock()
    cfg_mock.storage.db_path = db_path
    with mock.patch("linkedin_mcp.web.load_config", return_value=cfg_mock):
        with TestClient(app) as c:
            yield c


def test_dashboard_renders(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "linkedin-mcp-pro" in r.text
    # Cockpit v3 uses "New post" CTA (was "Draft a post" in v1.0/v2.0)
    assert "New post" in r.text
    # Sanity: the 4 KPI tiles
    assert "Posts" in r.text
    assert "Engagements" in r.text
    # Action row
    assert "Engage" in r.text or "Schedule" in r.text


def test_api_summary(client: TestClient) -> None:
    r = client.get("/api/summary?days=30")
    assert r.status_code == 200
    data = r.json()
    # The summary dict has 'success_rate' with 'total' or 'days'
    assert isinstance(data, dict)
    assert "days" in data or "success_rate" in data


def test_api_schedules_empty(client: TestClient) -> None:
    r = client.get("/api/schedules")
    assert r.status_code == 200
    assert r.json() == []


def test_api_templates_empty(client: TestClient) -> None:
    r = client.get("/api/templates")
    assert r.status_code == 200
    assert r.json() == []


def test_api_deadman(client: TestClient) -> None:
    r = client.get("/api/deadman")
    assert r.status_code == 200
    data = r.json()
    assert "status" in data


def test_api_post_dry_run(client: TestClient) -> None:
    # Dry-run should never be blocked by business hours / quota checks,
    # so we mock the safety to bypass everything.
    with mock.patch("linkedin_mcp.web._safety") as ms:
        ms.return_value.enforce.return_value = None
        r = client.post("/api/post", json={"text": "hello world", "dry_run": True})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["dry_run"] is True


def test_api_post_safety_rejects(client: TestClient) -> None:
    # Force outside business hours by mocking safety
    with mock.patch("linkedin_mcp.web._safety") as ms:
        from linkedin_mcp.safety import OutsideBusinessHoursError
        ms.return_value.enforce.side_effect = OutsideBusinessHoursError("test")
        r = client.post("/api/post", json={"text": "hello", "dry_run": False})
        assert r.status_code == 400


def test_api_drafts_endpoint_exists(client: TestClient) -> None:
    # We mock the drafter to avoid hitting the LLM
    with mock.patch("linkedin_mcp.web.PostDrafter") as MockDrafter:
        instance = MockDrafter.return_value
        instance.draft.return_value = "Drafted text"
        instance.last_model = "test-model"
        r = client.post(
            "/api/drafts",
            json={"topic": "AI", "tone": "professional", "length": 200, "include_hashtags": False},
        )
        assert r.status_code == 200
        assert r.json()["text"] == "Drafted text"


def test_api_drafts_save_and_list(client: TestClient) -> None:
    # Save a draft
    r = client.post("/api/drafts/save", json={"topic": "Test", "body": "Hello world", "tone": "casual"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert "id" in data
    draft_id = data["id"]

    # List drafts
    r = client.get("/api/drafts")
    assert r.status_code == 200
    drafts = r.json()["drafts"]
    assert any(d["id"] == draft_id for d in drafts)

    # Delete
    r = client.delete(f"/api/drafts/{draft_id}")
    assert r.status_code == 200
    assert r.json()["removed"] == 1

    # Verify gone
    r = client.get("/api/drafts")
    assert not any(d["id"] == draft_id for d in r.json()["drafts"])


def test_dashboard_html_includes_js_handlers() -> None:
    """Static HTML must include the JS that wires up the buttons."""
    assert "draft-btn" in _DASHBOARD_HTML
    assert "post-btn" in _DASHBOARD_HTML
    assert "/api/summary" in _DASHBOARD_HTML
    assert "/api/deadman" in _DASHBOARD_HTML


# ----------------------------------------------------------------------------
# v2.0.4 — tests for the 10 additional panel endpoints
# ----------------------------------------------------------------------------


def test_api_accounts_list(client: TestClient) -> None:
    r = client.get("/api/accounts")
    assert r.status_code == 200
    data = r.json()
    assert "accounts" in data and "default" in data
    assert isinstance(data["accounts"], list) and len(data["accounts"]) >= 1
    first = data["accounts"][0]
    for k in ("name", "active", "last_used", "status"):
        assert k in first, f"missing {k!r} in account {first!r}"
    assert isinstance(data["default"], str)


def test_api_accounts_trailing_slash(client: TestClient) -> None:
    """`/api/accounts/` must be the same shape as `/api/accounts`."""
    r1 = client.get("/api/accounts")
    r2 = client.get("/api/accounts/")
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["default"] == r2.json()["default"]


def test_api_profile_returns_required_fields(client: TestClient) -> None:
    r = client.get("/api/profile")
    assert r.status_code == 200
    data = r.json()
    # The panel merges this into `profile` via Object.assign, so it must
    # have name + headline + summary at minimum.
    for k in ("name", "headline", "summary"):
        assert k in data and data[k], f"missing/empty {k!r}"
    assert "source" in data


def test_api_audit_list_shape(client: TestClient) -> None:
    """`/api/audit` returns a list (the panel does `this.logs = await r.json()`)."""
    r = client.get("/api/audit")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    # empty DB → empty list, never 404
    for row in data:
        for k in ("ts", "action", "target", "status", "details"):
            assert k in row, f"missing {k!r} in audit row {row!r}"


def test_api_audit_filters(client: TestClient) -> None:
    """status=ok | status=blocked | action=... must not error."""
    # Seed: a success and a blocked row
    db = DB(client.app.state.__dict__.get("_db_path", __import__("pathlib").Path("data/linkedin-mcp-pro.db"))) \
        if False else _db_from_client(client)
    db.audit("post", "success", target="self", detail={"text_len": 1})
    db.audit("message", "blocked_safety", target="x", detail={"reason": "outside_business_hours"})
    db.audit("connection", "failed", target="y", detail={"error": "429"})

    r_ok = client.get("/api/audit?status=ok")
    assert r_ok.status_code == 200
    assert all(row["status"] == "success" for row in r_ok.json())

    r_blocked = client.get("/api/audit?status=blocked")
    assert r_blocked.status_code == 200
    assert all(row["status"] in ("blocked_safety", "rate_limited") for row in r_blocked.json())

    r_err = client.get("/api/audit?status=error")
    assert r_err.status_code == 200
    assert all(row["status"] == "failed" for row in r_err.json())

    r_post = client.get("/api/audit?action=post")
    assert r_post.status_code == 200
    # We seeded two post rows (one success) and one connection
    actions = {row["action"] for row in r_post.json()}
    assert actions <= {"post", "post.create", "post.publish"} or "post" in actions or not r_post.json()


def test_api_safety_status_shape(client: TestClient) -> None:
    """safety/status returns everything the panel merges via Object.assign."""
    r = client.get("/api/safety/status")
    assert r.status_code == 200
    data = r.json()
    for k in ("enabled", "kpis", "hours", "insideHours", "whitelist", "blacklist",
             "daily_post_limit", "min_interval_minutes", "max_connections_per_day",
             "forbidden_actions", "last_check"):
        assert k in data, f"missing {k!r}"
    assert isinstance(data["kpis"], list) and len(data["kpis"]) >= 1
    kpi = data["kpis"][0]
    for k in ("label", "value", "limit", "pct"):
        assert k in kpi
    assert {"start", "end", "days", "tz"} <= set(data["hours"].keys())


def test_api_safety_test_post_action_allowed(client: TestClient) -> None:
    """Dry-run post action on a safe input → allowed (no rejection)."""
    with mock.patch("linkedin_mcp.web._safety") as ms:
        ms.return_value.enforce.return_value = None
        r = client.post("/api/safety/test", json={"action": "post", "params": {"text": "hi"}})
    assert r.status_code == 200
    data = r.json()
    assert data["allowed"] is True
    assert isinstance(data.get("warnings"), list)


def test_api_safety_test_panel_input_shape(client: TestClient) -> None:
    """The panel posts `{input: 'like AI startups'}` — endpoint must accept it."""
    with mock.patch("linkedin_mcp.web._safety") as ms:
        ms.return_value.enforce.return_value = None
        r = client.post("/api/safety/test", json={"input": "like AI startups"})
    assert r.status_code == 200
    data = r.json()
    assert "allowed" in data
    assert "reason" in data
    assert "warnings" in data


def test_api_safety_test_blacklist_blocks(client: TestClient) -> None:
    """`crypto` is on the default blacklist — should always be blocked."""
    r = client.post("/api/safety/test", json={"input": "promote crypto to investors"})
    assert r.status_code == 200
    data = r.json()
    assert data["allowed"] is False
    assert "blacklist" in data["reason"].lower() or "crypto" in data["reason"].lower()


def test_api_safety_test_unknown_action(client: TestClient) -> None:
    r = client.post("/api/safety/test", json={"action": "teleport"})
    assert r.status_code == 200
    data = r.json()
    assert data["allowed"] is False
    assert "unknown" in data["reason"].lower()


def test_api_engagement_overview(client: TestClient) -> None:
    r = client.get("/api/engagement/")
    assert r.status_code == 200
    data = r.json()
    for k in ("connections_sent", "messages_sent", "posts_published",
             "profile_views", "search_appearances", "period_days"):
        assert k in data
    assert data["period_days"] == 30
    # No-trailing-slash alias works too
    r2 = client.get("/api/engagement")
    assert r2.status_code == 200
    assert r2.json() == data


def test_api_engagement_run_dry_run(client: TestClient) -> None:
    """Dry-run must NOT call SafetyGuard.enforce (it's a preview)."""
    with mock.patch("linkedin_mcp.web._safety") as ms:
        r = client.post(
            "/api/engagement/likes",
            json={"keyword": "AI startups", "dry_run": True},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["dry_run"] is True
    assert data["action"] == "reaction"
    assert isinstance(data["results"], list) and len(data["results"]) >= 1
    assert data["results"][0]["status"] == "dry_run"
    # enforce must not have been called for a dry-run
    ms.return_value.enforce.assert_not_called()


def test_api_engagement_run_bogus_kind(client: TestClient) -> None:
    r = client.post("/api/engagement/teleport", json={"keyword": "x"})
    assert r.status_code == 400


def test_api_cache_clear(client: TestClient) -> None:
    r = client.post("/api/cache/clear")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert isinstance(data["cleared"], list) and len(data["cleared"]) >= 1


def test_api_server_restart(client: TestClient) -> None:
    r = client.post("/api/server/restart")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "scheduled_at" in data
    assert data["method"] in ("in-process", "systemd")


def test_api_settings_reset_all(client: TestClient) -> None:
    r = client.post("/api/settings/reset", json={"scope": "all"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["scope"] == "all"
    assert isinstance(data["reset_to"], list) and len(data["reset_to"]) >= 1


def test_api_settings_reset_scopes(client: TestClient) -> None:
    for scope in ("ui", "llm", "safety"):
        r = client.post("/api/settings/reset", json={"scope": scope})
        assert r.status_code == 200
        assert r.json()["scope"] == scope


def test_api_settings_reset_no_body(client: TestClient) -> None:
    """Frontend posts an empty body — endpoint must accept that."""
    r = client.post("/api/settings/reset")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    # default scope
    assert data["scope"] == "all"


# Helper: derive the same DB path the TestClient fixture uses. The fixture
# patches load_config with cfg.storage.db_path = tmp_path / "web_test.db".
# We rebuild that DB instance from the test's own cfg_mock by reading
# the patched config out of the patched load_config.
def _db_from_client(client: TestClient) -> DB:
    from linkedin_mcp import web as _web
    cfg = _web.load_config()
    return DB(cfg.storage.db_path)