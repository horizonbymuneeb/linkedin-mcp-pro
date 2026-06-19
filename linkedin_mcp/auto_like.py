"""Auto-like: search posts by keyword, like the matches through SafetyGate.

This is the most-controlled Tier 3 feature because likes are the easiest
action to spam and the easiest to detect. The defaults are intentionally
low (30 likes/day, 5/hour) and dry_run=true.

Flow:
    search_by_keyword("AI agent") -> [Post, Post, ...]
    For each post:
        decision = gate.check("like", target=urn, target_text=post.text)
        if decision.allowed and not decision.dry_run:
            click_like_button(urn)
            time.sleep(cooldown)
        else:
            log denial

The `search_fn` and `like_fn` are injected, so the unit tests don't need
a real LinkedIn session.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .ban_safety import SafetyDecision, SafetyGate

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass
class PostTarget:
    """A LinkedIn post discovered via search."""

    urn: str
    author: str
    text: str
    url: str = ""
    reactions: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "urn": self.urn,
            "author": self.author,
            "text": self.text,
            "url": self.url,
            "reactions": self.reactions,
        }


@dataclass
class LikeActionResult:
    """Result of attempting to like a single post."""

    urn: str
    author: str
    decision: SafetyDecision
    executed: bool = False  # true if like button was actually clicked
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "urn": self.urn,
            "author": self.author,
            "decision": self.decision.to_dict(),
            "executed": self.executed,
            "error": self.error,
        }


@dataclass
class LikeCampaignResult:
    """Aggregate result of a keyword search + like cycle."""

    keyword: str
    found: int = 0
    allowed: int = 0
    executed: int = 0
    denied: int = 0
    dry_run: int = 0
    errors: int = 0
    results: list[LikeActionResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "keyword": self.keyword,
            "found": self.found,
            "allowed": self.allowed,
            "executed": self.executed,
            "denied": self.denied,
            "dry_run": self.dry_run,
            "errors": self.errors,
            "results": [r.to_dict() for r in self.results],
        }


# ---------------------------------------------------------------------------
# AutoLike
# ---------------------------------------------------------------------------


SearchFn = Callable[[str, int], list[PostTarget]]
LikeFn = Callable[[str], bool]


class AutoLike:
    """Run a keyword-based auto-like campaign through SafetyGate."""

    def __init__(self, gate: SafetyGate) -> None:
        self.gate = gate

    def run(
        self,
        keyword: str,
        *,
        search_fn: SearchFn,
        like_fn: LikeFn,
        max_results: int = 10,
    ) -> LikeCampaignResult:
        """Search for `keyword`, like each match through the gate.

        Args:
            keyword: search term
            search_fn: callable(keyword, max_results) -> list[PostTarget]
            like_fn: callable(urn) -> bool (True if like succeeded)
            max_results: how many search results to consider
        """
        result = LikeCampaignResult(keyword=keyword)
        posts = search_fn(keyword, max_results) or []
        result.found = len(posts)

        for post in posts:
            decision = self.gate.check(
                "like", target=post.urn, target_text=post.text
            )
            action_result = LikeActionResult(
                urn=post.urn, author=post.author, decision=decision
            )
            if not decision.allowed:
                if decision.dry_run:
                    result.dry_run += 1
                else:
                    result.denied += 1
            else:
                result.allowed += 1
                try:
                    ok = like_fn(post.urn)
                    if ok:
                        action_result.executed = True
                        result.executed += 1
                    else:
                        action_result.error = "like_fn returned False"
                        result.errors += 1
                except Exception as exc:
                    action_result.error = str(exc)
                    result.errors += 1
                    log.warning("Like failed for %s: %s", post.urn, exc)
            result.results.append(action_result)

        return result
