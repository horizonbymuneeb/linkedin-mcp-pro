"""Tests for A/B testing (v0.6.0)."""

from __future__ import annotations

from pathlib import Path

import pytest

from linkedin_mcp.ab_testing import ABTest, ABTestError, ABTestStore, Variant


@pytest.fixture
def store(tmp_path: Path) -> ABTestStore:
    return ABTestStore(tmp_path / "ab.yaml")


def _make(name: str = "t1") -> ABTest:
    return ABTest(
        name=name,
        variant_a=Variant(text="A: short"),
        variant_b=Variant(text="B: longer explanation"),
    )


def test_empty(store: ABTestStore) -> None:
    assert store.list_tests() == []


def test_save_and_get(store: ABTestStore) -> None:
    store.save(_make())
    assert store.get("t1").variant_a.text == "A: short"


def test_save_duplicate_overwrites(store: ABTestStore) -> None:
    store.save(_make())
    t = store.get("t1")
    t.variant_a.impressions = 50
    store.save(t)
    assert store.get("t1").variant_a.impressions == 50


def test_remove(store: ABTestStore) -> None:
    store.save(_make())
    assert store.remove("t1") is True
    assert store.remove("t1") is False


def test_record_impressions(store: ABTestStore) -> None:
    store.save(_make())
    store.record_impressions("t1", "a", 50)
    store.record_impressions("t1", "b", 50)
    t = store.get("t1")
    assert t.variant_a.impressions == 50
    assert t.variant_b.impressions == 50


def test_record_engagement(store: ABTestStore) -> None:
    store.save(_make())
    store.record_engagement("t1", "a", 10)
    store.record_engagement("t1", "b", 5)
    assert store.get("t1").variant_a.engagement == 10


def test_record_invalid_variant_raises(store: ABTestStore) -> None:
    store.save(_make())
    with pytest.raises(ABTestError):
        store.record_impressions("t1", "c", 10)


def test_chi_squared_equal_rates() -> None:
    chi2, p = ABTestStore.chi_squared_2x2(100, 10, 100, 10)
    assert chi2 == 0.0


def test_chi_squared_highly_skewed() -> None:
    chi2, p = ABTestStore.chi_squared_2x2(1000, 100, 1000, 50)
    assert chi2 > 15
    assert p <= 0.001


def test_chi_squared_degenerate_returns_one() -> None:
    chi2, p = ABTestStore.chi_squared_2x2(0, 0, 100, 10)
    assert chi2 == 0.0
    assert p == 1.0


def test_result_winner_a(store: ABTestStore) -> None:
    test = _make()
    test.variant_a.impressions = 200
    test.variant_a.engagement = 30  # 15% rate
    test.variant_b.impressions = 200
    test.variant_b.engagement = 10  # 5% rate
    test.target_impressions = 100
    store.save(test)
    r = store.result("t1")
    assert r["winner"] == "a"
    assert r["significant"] is True
    assert r["ready"] is True


def test_result_no_winner_low_data(store: ABTestStore) -> None:
    test = _make()
    test.variant_a.impressions = 5
    test.variant_b.impressions = 5
    store.save(test)
    r = store.result("t1")
    assert r["winner"] is None
    assert r["ready"] is False


def test_result_inconclusive(store: ABTestStore) -> None:
    test = _make()
    test.variant_a.impressions = 100
    test.variant_a.engagement = 10
    test.variant_b.impressions = 100
    test.variant_b.engagement = 8
    test.target_impressions = 100
    store.save(test)
    r = store.result("t1")
    assert "inconclusive" in (r["winner"] or "")


def test_persistence(store: ABTestStore, tmp_path: Path) -> None:
    store.save(_make("persist"))
    store2 = ABTestStore(tmp_path / "ab.yaml")
    assert [t.name for t in store2.list_tests()] == ["persist"]


def test_get_unknown_raises(store: ABTestStore) -> None:
    with pytest.raises(ABTestError):
        store.get("ghost")


def test_variant_from_dict_missing_text() -> None:
    v = Variant.from_dict({"impressions": 0, "engagement": 0})
    assert v.text == ""