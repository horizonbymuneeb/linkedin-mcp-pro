"""Multi-account support for linkedin-mcp-pro (v0.6.0).

Each account is a separate browser profile (separate cookies, separate
localStorage). Only one account is "active" at a time — the active
account's profile_dir is what the server uses for write actions.

WARNING: Running two accounts from the same IP in parallel is
LinkedIn-flaggable. This tool assumes you switch between accounts
sequentially, not in parallel.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml


class AccountError(Exception):
    """Raised for any account-store failure."""


@dataclass
class Account:
    name: str
    profile_dir: str
    description: str = ""
    active: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "profile_dir": self.profile_dir,
            "description": self.description,
            "active": self.active,
        }


class AccountManager:
    """File-backed multi-account registry.

    One YAML index at ``~/.linkedin-mcp/accounts.yaml`` listing every
    registered account. The active account is whatever's marked
    ``active: true`` (only one at a time).
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(
            path
            or os.environ.get("LINKEDIN_MCP_ACCOUNTS_FILE")
            or (Path.home() / ".linkedin-mcp" / "accounts.yaml")
        )

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"accounts": []}
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {"accounts": []}
        except yaml.YAMLError as e:
            raise AccountError(f"Invalid YAML in {self.path}: {e}") from e

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True, width=120)

    def list_accounts(self) -> list[Account]:
        data = self._read()
        return [Account(**a) for a in data.get("accounts", []) or []]

    def get(self, name: str) -> Account:
        for a in self.list_accounts():
            if a.name == name:
                return a
        raise AccountError(f"Account {name!r} not found")

    def register(self, name: str, profile_dir: str, description: str = "") -> Account:
        if not name or not name.strip():
            raise AccountError("Account name is required")
        if not profile_dir or not profile_dir.strip():
            raise AccountError("Account profile_dir is required")
        current = self.list_accounts()
        if any(a.name == name for a in current):
            raise AccountError(f"Account {name!r} already registered")
        active = len(current) == 0  # first account becomes active by default
        acc = Account(
            name=name.strip(),
            profile_dir=profile_dir.strip(),
            description=description,
            active=active,
        )
        current.append(acc)
        self._write({"accounts": [a.to_dict() for a in current]})
        return acc

    def remove(self, name: str) -> bool:
        current = self.list_accounts()
        new = [a for a in current if a.name != name]
        if len(new) == len(current):
            return False
        # If we removed the active one, activate the first remaining
        had_active = any(a.name == name and a.active for a in current)
        if had_active and new:
            new[0].active = True
        self._write({"accounts": [a.to_dict() for a in new]})
        return True

    def set_active(self, name: str) -> Account:
        current = self.list_accounts()
        target: Optional[Account] = None
        for a in current:
            a.active = (a.name == name)
            if a.active:
                target = a
        if target is None:
            raise AccountError(f"Account {name!r} not found")
        self._write({"accounts": [a.to_dict() for a in current]})
        return target

    def get_active(self) -> Optional[Account]:
        for a in self.list_accounts():
            if a.active:
                return a
        return None