"""MCP tools for A/B testing (v0.6.0)."""

from __future__ import annotations

from typing import Any

from ..ab_testing import ABTest, ABTestStore, Variant


def _store() -> ABTestStore:
    return ABTestStore()


def list_ab_tests() -> list[dict[str, Any]]:
    return [t.to_dict() for t in _store().list_tests()]


def create_ab_test(
    name: str,
    variant_a_text: str,
    variant_b_text: str,
    target_impressions: int = 100,
) -> dict[str, Any]:
    test = ABTest(
        name=name,
        variant_a=Variant(text=variant_a_text),
        variant_b=Variant(text=variant_b_text),
        target_impressions=target_impressions,
    )
    _store().save(test)
    return {"ok": True, "test": test.to_dict()}


def record_ab_impressions(name: str, variant: str, n: int) -> dict[str, Any]:
    test = _store().record_impressions(name, variant, n)
    return {"ok": True, "test": test.to_dict()}


def record_ab_engagement(name: str, variant: str, n: int) -> dict[str, Any]:
    test = _store().record_engagement(name, variant, n)
    return {"ok": True, "test": test.to_dict()}


def get_ab_test_result(name: str) -> dict[str, Any]:
    return _store().result(name)