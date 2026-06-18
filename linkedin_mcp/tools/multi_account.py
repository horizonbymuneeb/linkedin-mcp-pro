"""MCP tools for multi-account support (v0.6.0)."""

from __future__ import annotations

from typing import Any, Optional

from ..multi_account import Account, AccountError, AccountManager


def _mgr() -> AccountManager:
    return AccountManager()


def list_accounts() -> list[dict[str, Any]]:
    return [a.to_dict() for a in _mgr().list_accounts()]


def register_account(
    name: str,
    profile_dir: str,
    description: str = "",
) -> dict[str, Any]:
    try:
        a = _mgr().register(name, profile_dir, description)
    except AccountError as e:
        raise ValueError(str(e)) from e
    return {"ok": True, "account": a.to_dict()}


def remove_account(name: str) -> dict[str, Any]:
    if not _mgr().remove(name):
        raise ValueError(f"Account {name!r} not found")
    return {"ok": True, "removed": name}


def set_active_account(name: str) -> dict[str, Any]:
    try:
        a = _mgr().set_active(name)
    except AccountError as e:
        raise ValueError(str(e)) from e
    return {"ok": True, "account": a.to_dict()}


def get_active_account() -> dict[str, Any]:
    a = _mgr().get_active()
    if a is None:
        return {"active": None}
    return {"active": a.to_dict()}