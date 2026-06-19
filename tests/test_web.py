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
    assert "Draft a post" in r.text


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


def test_dashboard_html_includes_js_handlers() -> None:
    """Static HTML must include the JS that wires up the buttons."""
    assert "draft-btn" in _DASHBOARD_HTML
    assert "post-btn" in _DASHBOARD_HTML
    assert "/api/summary" in _DASHBOARD_HTML
    assert "/api/deadman" in _DASHBOARD_HTML