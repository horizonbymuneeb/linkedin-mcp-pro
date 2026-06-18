"""Tests for the multi-account manager (v0.6.0)."""

from __future__ import annotations

from pathlib import Path

import pytest

from linkedin_mcp.multi_account import Account, AccountError, AccountManager


@pytest.fixture
def mgr(tmp_path: Path) -> AccountManager:
    return AccountManager(tmp_path / "accounts.yaml")


def test_empty_list(mgr: AccountManager) -> None:
    assert mgr.list_accounts() == []
    assert mgr.get_active() is None


def test_register_first_account_is_active(mgr: AccountManager) -> None:
    a = mgr.register("personal", "/home/me/personal", "my main")
    assert a.active is True
    assert mgr.get_active().name == "personal"


def test_register_second_is_inactive(mgr: AccountManager) -> None:
    mgr.register("personal", "/p1")
    b = mgr.register("consulting", "/p2")
    assert b.active is False


def test_register_duplicate_raises(mgr: AccountManager) -> None:
    mgr.register("a", "/p1")
    with pytest.raises(AccountError):
        mgr.register("a", "/p2")


def test_register_requires_name(mgr: AccountManager) -> None:
    with pytest.raises(AccountError):
        mgr.register("", "/p1")


def test_register_requires_profile_dir(mgr: AccountManager) -> None:
    with pytest.raises(AccountError):
        mgr.register("a", "")


def test_set_active(mgr: AccountManager) -> None:
    mgr.register("a", "/p1")
    mgr.register("b", "/p2")
    target = mgr.set_active("b")
    assert target.active is True
    assert mgr.get_active().name == "b"
    assert mgr.get("a").active is False


def test_set_active_unknown_raises(mgr: AccountManager) -> None:
    mgr.register("a", "/p1")
    with pytest.raises(AccountError):
        mgr.set_active("ghost")


def test_remove(mgr: AccountManager) -> None:
    mgr.register("a", "/p1")
    mgr.register("b", "/p2")
    assert mgr.remove("a") is True
    assert mgr.remove("a") is False
    assert [a.name for a in mgr.list_accounts()] == ["b"]


def test_remove_active_promotes_another(mgr: AccountManager) -> None:
    mgr.register("a", "/p1")  # active
    mgr.register("b", "/p2")
    mgr.remove("a")
    assert mgr.get_active().name == "b"


def test_get_unknown_raises(mgr: AccountManager) -> None:
    with pytest.raises(AccountError):
        mgr.get("ghost")


def test_persistence(mgr: AccountManager, tmp_path: Path) -> None:
    mgr.register("a", "/p1", "first")
    # Re-instantiate from the same path
    mgr2 = AccountManager(tmp_path / "accounts.yaml")
    accounts = mgr2.list_accounts()
    assert [a.name for a in accounts] == ["a"]
    assert accounts[0].description == "first"
    assert accounts[0].active is True