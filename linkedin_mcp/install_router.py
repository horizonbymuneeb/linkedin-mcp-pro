"""FastAPI router exposing the install wizard over HTTP."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from .installer_helpers import (
    AGENT_CONFIGS,
    build_config_snippet,
    detect_installed_agents,
    doctor_report,
    install_to_agent,
    is_installed,
    resolve_config_path,
    uninstall_from_agent,
)

router = APIRouter(prefix="/api/install", tags=["install"])


@router.get("/doctor")
def doctor() -> dict[str, Any]:
    """Return environment diagnostics."""
    return doctor_report()


@router.get("/agents")
def agents() -> list[dict[str, Any]]:
    """List all supported agents with their install state."""
    detected = detect_installed_agents()
    out: list[dict[str, Any]] = []
    for name, cfg in AGENT_CONFIGS.items():
        out.append(
            {
                "id": name,
                "display_name": cfg["display_name"],
                "os_support": cfg["os_support"],
                "config_path": str(resolve_config_path(name)),
                "is_installed": is_installed(name),
                "config_present": detected.get(name, False),
            }
        )
    return out


@router.get("/agents/{agent}/config")
def agent_config(agent: str) -> dict[str, Any]:
    if agent not in AGENT_CONFIGS:
        raise HTTPException(status_code=404, detail=f"Unknown agent: {agent}")
    return build_config_snippet(agent)


@router.post("/install/{agent}")
def install(agent: str, dry_run: bool = False) -> dict[str, Any]:
    if agent not in AGENT_CONFIGS:
        raise HTTPException(status_code=404, detail=f"Unknown agent: {agent}")
    result = install_to_agent(agent, dry_run=dry_run)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result.get("error", "install failed"))
    return result


@router.delete("/uninstall/{agent}")
def uninstall(agent: str) -> dict[str, Any]:
    if agent not in AGENT_CONFIGS:
        raise HTTPException(status_code=404, detail=f"Unknown agent: {agent}")
    result = uninstall_from_agent(agent)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result.get("error", "uninstall failed"))
    return result


@router.get("/verify/{agent}")
def verify(agent: str) -> dict[str, Any]:
    if agent not in AGENT_CONFIGS:
        raise HTTPException(status_code=404, detail=f"Unknown agent: {agent}")
    return {
        "agent": agent,
        "is_installed": is_installed(agent),
        "config_path": str(resolve_config_path(agent)),
    }
