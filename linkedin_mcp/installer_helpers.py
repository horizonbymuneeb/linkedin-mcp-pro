"""Helpers for installing the LinkedIn MCP server into any supported MCP agent.

The installer is intentionally agent-agnostic: it knows the on-disk layout of
each supported MCP host, can detect which ones are present on the current
machine, and can atomically merge / remove the LinkedIn MCP entry without
disturbing the host's other MCP servers.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ENTRY_NAME = "linkedin-mcp-pro"


AGENT_CONFIGS: dict[str, dict[str, Any]] = {
    "claude-desktop-mac": {
        "display_name": "Claude Desktop (macOS)",
        "os_support": ["darwin"],
        "config_path_template": "{home}/Library/Application Support/Claude/claude_desktop_config.json",
        "mcp_root_key": "mcpServers",
        "is_file_based": True,
        "merge_strategy": "mcpServers",
    },
    "claude-desktop-linux": {
        "display_name": "Claude Desktop (Linux)",
        "os_support": ["linux"],
        "config_path_template": "{home}/.config/Claude/claude_desktop_config.json",
        "mcp_root_key": "mcpServers",
        "is_file_based": True,
        "merge_strategy": "mcpServers",
    },
    "claude-desktop-win": {
        "display_name": "Claude Desktop (Windows)",
        "os_support": ["windows"],
        "config_path_template": "{home}/AppData/Roaming/Claude/claude_desktop_config.json",
        "mcp_root_key": "mcpServers",
        "is_file_based": True,
        "merge_strategy": "mcpServers",
    },
    "claude-code": {
        "display_name": "Claude Code",
        "os_support": ["darwin", "linux", "windows"],
        "config_path_template": "{home}/.claude.json",
        "mcp_root_key": "mcpServers",
        "is_file_based": True,
        "merge_strategy": "mcpServers",
    },
    "cursor": {
        "display_name": "Cursor",
        "os_support": ["darwin", "linux", "windows"],
        "config_path_template": "{home}/.cursor/mcp.json",
        "mcp_root_key": "mcpServers",
        "is_file_based": True,
        "merge_strategy": "mcpServers",
    },
    "cline": {
        "display_name": "Cline (VS Code)",
        "os_support": ["darwin", "linux", "windows"],
        "config_path_template": "{home}/.cline/mcp_settings.json",
        "mcp_root_key": "mcpServers",
        "is_file_based": True,
        "merge_strategy": "mcpServers",
    },
    "continue": {
        "display_name": "Continue.dev",
        "os_support": ["darwin", "linux", "windows"],
        "config_path_template": "{home}/.continue/config.json",
        "mcp_root_key": "mcpServers",
        "is_file_based": True,
        "merge_strategy": "mcpServers",
    },
    "windsurf": {
        "display_name": "Windsurf",
        "os_support": ["darwin", "linux", "windows"],
        "config_path_template": "{home}/.codeium/windsurf/mcp_config.json",
        "mcp_root_key": "mcpServers",
        "is_file_based": True,
        "merge_strategy": "mcpServers",
    },
    "zed": {
        "display_name": "Zed",
        "os_support": ["darwin", "linux", "windows"],
        "config_path_template": "{home}/.config/zed/settings.json",
        "mcp_root_key": "context_servers",
        "is_file_based": True,
        "merge_strategy": "mcpServers",
    },
    "open-webui": {
        "display_name": "Open WebUI",
        "os_support": ["darwin", "linux", "windows"],
        "config_path_template": "{home}/.open-webui/mcp_servers.json",
        "mcp_root_key": "mcp_servers",
        "is_file_based": True,
        "merge_strategy": "mcp_servers",
    },
    "librechat": {
        "display_name": "LibreChat",
        "os_support": ["darwin", "linux", "windows"],
        "config_path_template": "{home}/.librechat/librechat.yaml",
        "mcp_root_key": "mcpServers",
        "is_file_based": True,
        "merge_strategy": "mcpServers",
    },
    "lobechat": {
        "display_name": "LobeChat",
        "os_support": ["darwin", "linux", "windows"],
        "config_path_template": "{home}/.lobe-chat/mcp.json",
        "mcp_root_key": "mcpServers",
        "is_file_based": True,
        "merge_strategy": "mcpServers",
    },
}


def resolve_config_path(agent: str) -> Path:
    """Expand {home} for the given agent."""
    if agent not in AGENT_CONFIGS:
        raise KeyError(f"Unknown agent: {agent}")
    template = AGENT_CONFIGS[agent]["config_path_template"]
    return Path(os.path.expanduser(template.format(home=str(Path.home()))))


def detect_installed_agents() -> dict[str, bool]:
    """Return a mapping of agent_name -> config file present on disk."""
    result: dict[str, bool] = {}
    for name, cfg in AGENT_CONFIGS.items():
        if not cfg["is_file_based"]:
            result[name] = False
            continue
        try:
            result[name] = resolve_config_path(name).exists()
        except Exception:  # pragma: no cover - defensive
            result[name] = False
    return result


def build_config_snippet(agent: str, command: str = ENTRY_NAME) -> dict[str, Any]:
    """Return the snippet that should be merged into ``mcp_root_key``."""
    if agent not in AGENT_CONFIGS:
        raise KeyError(f"Unknown agent: {agent}")
    mcp_root_key = AGENT_CONFIGS[agent]["mcp_root_key"]
    server_entry: dict[str, Any] = {
        "command": command,
        "args": ["serve"],
    }
    return {mcp_root_key: {ENTRY_NAME: server_entry}}


def merge_config(existing: dict[str, Any], snippet: dict[str, Any], mcp_root_key: str) -> dict[str, Any]:
    """Atomically merge ``snippet`` into ``existing`` preserving other servers."""
    merged = dict(existing) if existing else {}
    existing_servers = dict(merged.get(mcp_root_key, {}) or {})
    snippet_servers = dict(snippet.get(mcp_root_key, {}) or {})
    existing_servers.update(snippet_servers)
    merged[mcp_root_key] = existing_servers
    return merged


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
            if not isinstance(data, dict):
                return {}
            return data
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def install_to_agent(agent: str, dry_run: bool = False) -> dict[str, Any]:
    """Install / refresh our MCP entry in the agent's config file."""
    if agent not in AGENT_CONFIGS:
        return {"ok": False, "agent": agent, "error": "unknown_agent"}

    cfg = AGENT_CONFIGS[agent]
    if not cfg["is_file_based"]:
        return {"ok": False, "agent": agent, "error": "not_file_based"}

    path = resolve_config_path(agent)
    existing = _load_json(path)
    snippet = build_config_snippet(agent)
    merged = merge_config(existing, snippet, cfg["mcp_root_key"])

    if dry_run:
        return {
            "ok": True,
            "agent": agent,
            "path": str(path),
            "dry_run": True,
            "would_write": merged,
        }

    try:
        _write_json(path, merged)
    except OSError as exc:
        return {"ok": False, "agent": agent, "path": str(path), "error": str(exc)}

    return {"ok": True, "agent": agent, "path": str(path)}


def uninstall_from_agent(agent: str) -> dict[str, Any]:
    """Remove our entry from the agent's config file (best-effort)."""
    if agent not in AGENT_CONFIGS:
        return {"ok": False, "agent": agent, "error": "unknown_agent"}

    cfg = AGENT_CONFIGS[agent]
    if not cfg["is_file_based"]:
        return {"ok": False, "agent": agent, "error": "not_file_based"}

    path = resolve_config_path(agent)
    existing = _load_json(path)
    servers = existing.get(cfg["mcp_root_key"], {}) or {}
    if ENTRY_NAME in servers:
        servers.pop(ENTRY_NAME)
        existing[cfg["mcp_root_key"]] = servers
        try:
            _write_json(path, existing)
        except OSError as exc:
            return {"ok": False, "agent": agent, "path": str(path), "error": str(exc)}
        return {"ok": True, "agent": agent, "path": str(path), "removed": True}
    return {"ok": True, "agent": agent, "path": str(path), "removed": False}


def is_installed(agent: str) -> bool:
    """Return True if our entry already exists in the agent's config."""
    if agent not in AGENT_CONFIGS:
        return False
    cfg = AGENT_CONFIGS[agent]
    if not cfg["is_file_based"]:
        return False
    path = resolve_config_path(agent)
    data = _load_json(path)
    return ENTRY_NAME in (data.get(cfg["mcp_root_key"], {}) or {})


def doctor_report() -> dict[str, Any]:
    """Collect environment diagnostics used by the CLI / API."""
    profile_dir = Path.home() / ".linkedin-mcp" / "profile"
    return {
        "python_version": sys.version.split()[0],
        "python_ok": sys.version_info >= (3, 11),
        "platform": sys.platform,
        "profile_dir": str(profile_dir),
        "profile_dir_exists": profile_dir.exists(),
        "li_at_cookie_present": bool(os.environ.get("LI_AT")),
        "agents_detected": detect_installed_agents(),
    }
