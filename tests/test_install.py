"""Tests for the install wizard (helpers, CLI, API)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from linkedin_mcp import install as install_cli
from linkedin_mcp import install_router
from linkedin_mcp.installer_helpers import (
    AGENT_CONFIGS,
    ENTRY_NAME,
    build_config_snippet,
    detect_installed_agents,
    doctor_report,
    install_to_agent,
    is_installed,
    merge_config,
    resolve_config_path,
    uninstall_from_agent,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    """Redirect Path.home() to a temp dir so configs are isolated."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# AGENT_CONFIGS
# ---------------------------------------------------------------------------


def test_agent_configs_has_at_least_12_entries():
    assert len(AGENT_CONFIGS) >= 12


@pytest.mark.parametrize("name,cfg", list(AGENT_CONFIGS.items()))
def test_each_agent_has_required_fields(name, cfg):
    assert "display_name" in cfg and cfg["display_name"]
    assert "os_support" in cfg and isinstance(cfg["os_support"], list) and cfg["os_support"]
    assert "config_path_template" in cfg and "{home}" in cfg["config_path_template"]
    assert "mcp_root_key" in cfg and cfg["mcp_root_key"]
    assert "is_file_based" in cfg
    assert "merge_strategy" in cfg


# ---------------------------------------------------------------------------
# build_config_snippet / merge_config
# ---------------------------------------------------------------------------


def test_build_config_snippet_returns_dict():
    snippet = build_config_snippet("cursor")
    assert isinstance(snippet, dict)
    assert "mcpServers" in snippet
    assert ENTRY_NAME in snippet["mcpServers"]
    assert snippet["mcpServers"][ENTRY_NAME]["command"] == "linkedin-mcp-pro"


def test_build_config_snippet_custom_command():
    snippet = build_config_snippet("cursor", command="custom-cmd")
    assert snippet["mcpServers"][ENTRY_NAME]["command"] == "custom-cmd"


def test_build_config_snippet_unknown_agent():
    with pytest.raises(KeyError):
        build_config_snippet("not-a-real-agent")


def test_merge_config_preserves_other_servers():
    existing = {"mcpServers": {"other-server": {"command": "x"}}, "theme": "dark"}
    snippet = build_config_snippet("cursor")
    merged = merge_config(existing, snippet, "mcpServers")
    assert "other-server" in merged["mcpServers"]
    assert ENTRY_NAME in merged["mcpServers"]
    assert merged["theme"] == "dark"


def test_merge_config_handles_empty_existing():
    merged = merge_config({}, build_config_snippet("cursor"), "mcpServers")
    assert ENTRY_NAME in merged["mcpServers"]


def test_merge_config_handles_empty_servers():
    existing = {"mcpServers": {}}
    snippet = build_config_snippet("cursor")
    merged = merge_config(existing, snippet, "mcpServers")
    assert ENTRY_NAME in merged["mcpServers"]


def test_merge_config_is_idempotent():
    snippet = build_config_snippet("cursor")
    once = merge_config({}, snippet, "mcpServers")
    twice = merge_config(once, snippet, "mcpServers")
    assert once == twice


# ---------------------------------------------------------------------------
# install_to_agent / uninstall_from_agent
# ---------------------------------------------------------------------------


def test_install_writes_config_to_disk(fake_home):
    result = install_to_agent("cursor")
    assert result["ok"], result
    path = Path(result["path"])
    assert path.exists()
    data = json.loads(path.read_text())
    assert ENTRY_NAME in data["mcpServers"]


def test_install_creates_parent_dirs(fake_home):
    install_to_agent("claude-desktop-linux")
    path = resolve_config_path("claude-desktop-linux")
    assert path.exists()
    assert path.parent.is_dir()


def test_install_dry_run_does_not_write(fake_home):
    result = install_to_agent("cursor", dry_run=True)
    assert result["ok"] and result["dry_run"] is True
    path = resolve_config_path("cursor")
    assert not path.exists()


def test_install_idempotent(fake_home):
    install_to_agent("cursor")
    install_to_agent("cursor")
    path = resolve_config_path("cursor")
    data = json.loads(path.read_text())
    assert list(data["mcpServers"].keys()) == [ENTRY_NAME]


def test_install_preserves_other_servers(fake_home):
    path = resolve_config_path("cursor")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": {"keep": {"command": "k"}}}))
    install_to_agent("cursor")
    data = json.loads(path.read_text())
    assert "keep" in data["mcpServers"]
    assert ENTRY_NAME in data["mcpServers"]


def test_uninstall_removes_entry(fake_home):
    install_to_agent("cursor")
    assert is_installed("cursor")
    res = uninstall_from_agent("cursor")
    assert res["ok"] and res["removed"] is True
    assert not is_installed("cursor")


def test_uninstall_no_entry_is_ok(fake_home):
    res = uninstall_from_agent("cursor")
    assert res["ok"] and res["removed"] is False


def test_install_unknown_agent_returns_error(fake_home):
    res = install_to_agent("nope")
    assert res["ok"] is False
    assert res["error"] == "unknown_agent"


def test_uninstall_unknown_agent_returns_error(fake_home):
    res = uninstall_from_agent("nope")
    assert res["ok"] is False


# ---------------------------------------------------------------------------
# detect_installed_agents / is_installed / doctor_report
# ---------------------------------------------------------------------------


def test_detect_installed_agents_returns_bools(fake_home):
    out = detect_installed_agents()
    assert isinstance(out, dict)
    for v in out.values():
        assert isinstance(v, bool)


def test_is_installed_false_when_missing(fake_home):
    assert is_installed("cursor") is False


def test_doctor_report_has_expected_keys():
    report = doctor_report()
    for k in (
        "python_version",
        "python_ok",
        "platform",
        "profile_dir",
        "profile_dir_exists",
        "li_at_cookie_present",
        "agents_detected",
    ):
        assert k in report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_doctor_runs():
    result = subprocess.run(
        [sys.executable, "-m", "linkedin_mcp.install", "doctor"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "python_version" in result.stdout
    assert "Detected agents" in result.stdout


def test_cli_list_runs():
    result = subprocess.run(
        [sys.executable, "-m", "linkedin_mcp.install", "list"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "cursor" in result.stdout


def test_cli_print_configs_runs():
    result = subprocess.run(
        [sys.executable, "-m", "linkedin_mcp.install", "print-configs"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "### " in result.stdout
    assert "linkedin-mcp-pro" in result.stdout


def test_main_invokable():
    # The Click group should be invokable as `main()`.
    assert callable(install_cli.main)


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------


def test_api_doctor_endpoint():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(install_router.router)
    client = TestClient(app)
    r = client.get("/api/install/doctor")
    assert r.status_code == 200
    assert "python_version" in r.json()


def test_api_agents_endpoint(fake_home):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(install_router.router)
    client = TestClient(app)
    r = client.get("/api/install/agents")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list) and len(body) >= 12
    for entry in body:
        assert {"id", "display_name", "os_support", "config_path", "is_installed"} <= entry.keys()


def test_api_install_and_uninstall(fake_home):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(install_router.router)
    client = TestClient(app)
    r = client.post("/api/install/install/cursor")
    assert r.status_code == 200
    assert resolve_config_path("cursor").exists()
    r2 = client.delete("/api/install/uninstall/cursor")
    assert r2.status_code == 200
    assert not is_installed("cursor")


def test_api_unknown_agent_404():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(install_router.router)
    client = TestClient(app)
    assert client.post("/api/install/install/nope").status_code == 404
    assert client.delete("/api/install/uninstall/nope").status_code == 404


def test_api_verify_endpoint(fake_home):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(install_router.router)
    client = TestClient(app)
    assert client.get("/api/install/verify/cursor").json()["is_installed"] is False
    client.post("/api/install/install/cursor")
    assert client.get("/api/install/verify/cursor").json()["is_installed"] is True


def test_api_agent_config_endpoint():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(install_router.router)
    client = TestClient(app)
    r = client.get("/api/install/agents/cursor/config")
    assert r.status_code == 200
    assert ENTRY_NAME in r.json()["mcpServers"]
