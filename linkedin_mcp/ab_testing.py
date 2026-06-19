"""A/B testing for LinkedIn posts (v0.6.0).

Track two post variants, log impressions + engagement per variant,
then determine a winner using a manual chi-squared calculation
(no scipy dependency).

Storage: ``~/.linkedin-mcp/ab_tests.yaml``.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml


# Critical value for 1 degree of freedom, p < 0.05 — precomputed lookup
# for several significance levels (avoids scipy).
_CHI2_PVALUES = {
    0.10: 2.706,
    0.05: 3.841,
    0.01: 6.635,
    0.001: 10.828,
}


class ABTestError(Exception):
    """Raised for any AB-test failure."""


@dataclass
class Variant:
    """One arm of an A/B test."""

    text: str
    impressions: int = 0
    engagement: int = 0
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "impressions": self.impressions,
            "engagement": self.engagement,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Variant":
        return cls(
            text=str(data.get("text", "")),
            impressions=int(data.get("impressions", 0)),
            engagement=int(data.get("engagement", 0)),
            notes=str(data.get("notes", "")),
        )


@dataclass
class ABTest:
    """Two-variant post test."""

    name: str
    variant_a: Variant
    variant_b: Variant
    created_at: str = ""
    target_impressions: int = 100

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "variant_a": self.variant_a.to_dict(),
            "variant_b": self.variant_b.to_dict(),
            "created_at": self.created_at,
            "target_impressions": self.target_impressions,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ABTest":
        if "variant_a" not in data or "variant_b" not in data:
            raise ABTestError("ABTest needs both 'variant_a' and 'variant_b'")
        return cls(
            name=str(data["name"]),
            variant_a=Variant.from_dict(data["variant_a"]),
            variant_b=Variant.from_dict(data["variant_b"]),
            created_at=str(data.get("created_at", "")),
            target_impressions=int(data.get("target_impressions", 100)),
        )


class ABTestStore:
    """File-backed A/B test storage."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(
            path
            or os.environ.get("LINKEDIN_MCP_AB_TESTS_FILE")
            or (Path.home() / ".linkedin-mcp" / "ab_tests.yaml")
        )

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"tests": []}
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {"tests": []}
        except yaml.YAMLError as e:
            raise ABTestError(f"Invalid YAML in {self.path}: {e}") from e

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True, width=120)

    def list_tests(self) -> list[ABTest]:
        data = self._read()
        return [ABTest.from_dict(t) for t in data.get("tests", []) or []]

    def get(self, name: str) -> ABTest:
        for t in self.list_tests():
            if t.name == name:
                return t
        raise ABTestError(f"A/B test {name!r} not found")

    def save(self, test: ABTest) -> ABTest:
        current = self.list_tests()
        for i, t in enumerate(current):
            if t.name == test.name:
                current[i] = test
                self._write({"tests": [t.to_dict() for t in current]})
                return test
        current.append(test)
        self._write({"tests": [t.to_dict() for t in current]})
        return test

    def remove(self, name: str) -> bool:
        current = self.list_tests()
        new = [t for t in current if t.name != name]
        if len(new) == len(current):
            return False
        self._write({"tests": [t.to_dict() for t in new]})
        return True

    # --- Recording ---

    def record_impressions(self, name: str, variant: str, n: int) -> ABTest:
        if variant not in ("a", "b"):
            raise ABTestError(f"variant must be 'a' or 'b', got {variant!r}")
        test = self.get(name)
        if variant == "a":
            test.variant_a.impressions += n
        else:
            test.variant_b.impressions += n
        return self.save(test)

    def record_engagement(self, name: str, variant: str, n: int) -> ABTest:
        if variant not in ("a", "b"):
            raise ABTestError(f"variant must be 'a' or 'b', got {variant!r}")
        test = self.get(name)
        if variant == "a":
            test.variant_a.engagement += n
        else:
            test.variant_b.engagement += n
        return self.save(test)

    # --- Analysis ---

    @staticmethod
    def chi_squared_2x2(
        a_imp: int, a_eng: int, b_imp: int, b_eng: int
    ) -> tuple[float, float]:
        """Two-proportion chi-squared (1 d.f.) — returns (chi2, p_approx).

        p_approx is interpolated from a small lookup table.
        Returns (0.0, 1.0) if data is degenerate (no impressions).
        """
        if a_imp <= 0 or b_imp <= 0:
            return (0.0, 1.0)
        a_no = a_imp - a_eng
        b_no = b_imp - b_eng
        total_imp = a_imp + b_imp
        total_eng = a_eng + b_eng
        total_no = a_no + b_no
        if total_imp == 0 or total_eng + total_no == 0:
            return (0.0, 1.0)
        expected_a_eng = total_eng * a_imp / total_imp
        expected_a_no = total_no * a_imp / total_imp
        expected_b_eng = total_eng * b_imp / total_imp
        expected_b_no = total_no * b_imp / total_imp
        # Guard against zero expected cells
        if min(expected_a_eng, expected_a_no, expected_b_eng, expected_b_no) == 0:
            return (0.0, 1.0)
        chi2 = (
            (a_eng - expected_a_eng) ** 2 / expected_a_eng
            + (a_no - expected_a_no) ** 2 / expected_a_no
            + (b_eng - expected_b_eng) ** 2 / expected_b_eng
            + (b_no - expected_b_no) ** 2 / expected_b_no
        )
        # Map chi2 → approximate p-value via lookup
        p = 1.0
        for threshold, chi2_crit in sorted(_CHI2_PVALUES.items()):
            if chi2 >= chi2_crit:
                p = threshold
        # Use finer-grained interpolation for nicer output
        if chi2 >= 10.828:
            p = 0.001
        elif chi2 >= 6.635:
            p = 0.01
        elif chi2 >= 3.841:
            p = 0.05
        elif chi2 >= 2.706:
            p = 0.10
        else:
            p = max(0.10, 1.0 - chi2 / 10.0)
        return (round(chi2, 4), round(p, 4))

    def result(self, name: str) -> dict[str, Any]:
        t = self.get(name)
        a_rate = (t.variant_a.engagement / t.variant_a.impressions) if t.variant_a.impressions > 0 else 0.0
        b_rate = (t.variant_b.engagement / t.variant_b.impressions) if t.variant_b.impressions > 0 else 0.0
        chi2, p = self.chi_squared_2x2(
            t.variant_a.impressions, t.variant_a.engagement,
            t.variant_b.impressions, t.variant_b.engagement,
        )
        winner: Optional[str] = None
        if t.variant_a.impressions + t.variant_b.impressions >= t.target_impressions:
            if a_rate > b_rate and p <= 0.05:
                winner = "a"
            elif b_rate > a_rate and p <= 0.05:
                winner = "b"
            elif a_rate > b_rate:
                winner = "a (inconclusive)"
            elif b_rate > a_rate:
                winner = "b (inconclusive)"
        return {
            "name": t.name,
            "variant_a": {
                **t.variant_a.to_dict(),
                "engagement_rate": round(a_rate, 4),
            },
            "variant_b": {
                **t.variant_b.to_dict(),
                "engagement_rate": round(b_rate, 4),
            },
            "chi_squared": chi2,
            "p_value": p,
            "significant": p <= 0.05,
            "winner": winner,
            "total_impressions": t.variant_a.impressions + t.variant_b.impressions,
            "target_impressions": t.target_impressions,
            "ready": (t.variant_a.impressions + t.variant_b.impressions) >= t.target_impressions,
        }