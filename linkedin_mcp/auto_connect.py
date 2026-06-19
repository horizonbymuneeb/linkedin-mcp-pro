"""Auto-connect: find people matching criteria, send connection requests.

Even higher ban risk than commenting — LinkedIn tracks "who you tried to
connect with" closely. Defaults:

    daily limit: 20 connection requests
    hourly limit: 3 requests
    cooldown: 120-600 seconds (3-10 minutes between requests)
    blacklist phrases in headline/title
    max headline length check
    min profile completeness (heuristic: must have headline + 50+ char about)
    always include a personalized note (no blank "I'd like to add you")
    note length: 200-300 chars
    note can NOT contain URLs, sales pitches, or self-promotion

Flow:
    search_by_criteria(role="ML Engineer", location="Pakistan") -> [Person, ...]
    For each person:
        if profile incomplete: skip
        if blacklist phrase in headline: skip
        if already requested/connected: skip
        note = personalize_fn(person)  # AI generates note
        if note fails safety checks: skip
        decision = gate.check("connect", target=urn, target_text=headline)
        if allowed and not dry_run:
            connect_fn(urn, note)
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .ban_safety import SafetyDecision, SafetyGate

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

# Phrases that indicate the person is a recruiter, agency, job-seeker spammer.
# We still might want to connect with real recruiters, but those should be
# intentional — auto-connect skips them by default.
DEFAULT_BLACKLIST_TERMS = [
    "recruiter",
    "talent acquisition",
    "staffing",
    "headhunter",
    "agency",
    "consultancy",
    "mlm",
    "crypto influencer",
    "life coach",
    "motivation speaker",
    "crypto",
    "forex",
    "dropshipping",
    "founder & ceo at"  # too-generic CEO posts; user can whitelist
]

MAX_NOTE_LENGTH = 300
MIN_NOTE_LENGTH = 80
MIN_HEADLINE_LENGTH = 5
MIN_ABOUT_LENGTH = 50


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass
class PersonTarget:
    """A LinkedIn profile matched by search criteria."""

    urn: str
    name: str
    headline: str = ""
    about: str = ""
    location: str = ""
    is_already_connected: bool = False
    is_already_invited: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "urn": self.urn,
            "name": self.name,
            "headline": self.headline,
            "about": self.about,
            "location": self.location,
            "is_already_connected": self.is_already_connected,
            "is_already_invited": self.is_already_invited,
        }


@dataclass
class ConnectActionResult:
    urn: str
    name: str
    decision: SafetyDecision
    note: str = ""
    skip_reason: str = ""
    executed: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "urn": self.urn,
            "name": self.name,
            "decision": self.decision.to_dict(),
            "note": self.note,
            "skip_reason": self.skip_reason,
            "executed": self.executed,
            "error": self.error,
        }


@dataclass
class ConnectCampaignResult:
    criteria: dict[str, Any]
    found: int = 0
    eligible: int = 0
    drafted: int = 0
    allowed: int = 0
    executed: int = 0
    denied: int = 0
    dry_run: int = 0
    skipped: int = 0
    errors: int = 0
    results: list[ConnectActionResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "criteria": self.criteria,
            "found": self.found,
            "eligible": self.eligible,
            "drafted": self.drafted,
            "allowed": self.allowed,
            "executed": self.executed,
            "denied": self.denied,
            "dry_run": self.dry_run,
            "skipped": self.skipped,
            "errors": self.errors,
            "results": [r.to_dict() for r in self.results],
        }


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------


class ConnectFilter:
    """Pre-screen profiles and notes before they reach the safety gate."""

    def __init__(
        self,
        *,
        blacklist_terms: list[str] | None = None,
        min_headline_length: int = MIN_HEADLINE_LENGTH,
        min_about_length: int = MIN_ABOUT_LENGTH,
        min_note_length: int = MIN_NOTE_LENGTH,
        max_note_length: int = MAX_NOTE_LENGTH,
    ) -> None:
        self.blacklist_terms = [
            t.lower() for t in (blacklist_terms if blacklist_terms is not None else DEFAULT_BLACKLIST_TERMS)
        ]
        self.min_headline_length = min_headline_length
        self.min_about_length = min_about_length
        self.min_note_length = min_note_length
        self.max_note_length = max_note_length

    def is_eligible(self, person: PersonTarget) -> tuple[bool, str]:
        if person.is_already_connected:
            return False, "already connected"
        if person.is_already_invited:
            return False, "already invited recently"
        if len(person.headline.strip()) < self.min_headline_length:
            return False, f"headline too short ({len(person.headline.strip())} chars)"
        if len(person.about.strip()) < self.min_about_length:
            return False, f"about too short ({len(person.about.strip())} chars)"
        if self._has_blacklist(person.headline):
            return False, "headline contains blacklist term"
        if self._has_blacklist(person.about):
            return False, "about contains blacklist term"
        return True, "ok"

    def is_safe_note(self, note: str) -> tuple[bool, str]:
        if len(note) < self.min_note_length:
            return False, f"note too short ({len(note)} chars)"
        if len(note) > self.max_note_length:
            return False, f"note too long ({len(note)} chars)"
        if re.search(r"https?://", note):
            return False, "note contains URL"
        if re.search(r"@[A-Za-z0-9_]{2,}", note):
            return False, "note contains @-mention"
        if self._has_blacklist(note):
            return False, "note contains blacklist term"
        # No "I'd like to add you to my network" generic phrasing
        generic_phrases = [
            "i'd like to add you",
            "i would like to add you",
            "let's connect",
            "expand my network",
            "grow my network",
            "i came across your profile",
        ]
        note_lc = note.lower()
        for p in generic_phrases:
            if p in note_lc:
                return False, f"note has generic phrase: {p!r}"
        return True, "ok"

    def _has_blacklist(self, text: str) -> bool:
        text_lc = text.lower()
        return any(t in text_lc for t in self.blacklist_terms)


# ---------------------------------------------------------------------------
# Auto-connect
# ---------------------------------------------------------------------------


SearchFn = Callable[[dict[str, Any], int], list[PersonTarget]]
NoteFn = Callable[[PersonTarget], str]
ConnectFn = Callable[[str, str], bool]


class AutoConnect:
    """Run a criteria-based auto-connect campaign through safety filters + gate."""

    def __init__(self, gate: SafetyGate, filter_: ConnectFilter | None = None) -> None:
        self.gate = gate
        self.filter = filter_ or ConnectFilter()

    def run(
        self,
        criteria: dict[str, Any],
        *,
        search_fn: SearchFn,
        note_fn: NoteFn,
        connect_fn: ConnectFn,
        max_results: int = 20,
    ) -> ConnectCampaignResult:
        """Search → filter → draft note → gate → send request."""
        result = ConnectCampaignResult(criteria=criteria)
        people = search_fn(criteria, max_results) or []
        result.found = len(people)

        for person in people:
            ar = ConnectActionResult(
                urn=person.urn, name=person.name,
                decision=SafetyDecision(
                    allowed=False, reason="not evaluated",
                    action_type="connect", target=person.urn,
                ),
            )

            eligible, reason = self.filter.is_eligible(person)
            if not eligible:
                ar.skip_reason = reason
                result.skipped += 1
                result.results.append(ar)
                continue
            result.eligible += 1

            # Generate note
            try:
                note = note_fn(person)
            except Exception as exc:
                ar.skip_reason = f"note error: {exc}"
                ar.error = str(exc)
                result.errors += 1
                result.results.append(ar)
                continue
            ar.note = note
            result.drafted += 1

            # Filter note
            safe, reason = self.filter.is_safe_note(note)
            if not safe:
                ar.skip_reason = reason
                result.skipped += 1
                result.results.append(ar)
                continue

            # Gate check
            decision = self.gate.check(
                "connect", target=person.urn, target_text=person.headline
            )
            ar.decision = decision
            if not decision.allowed:
                if decision.dry_run:
                    result.dry_run += 1
                else:
                    result.denied += 1
                result.results.append(ar)
                continue
            result.allowed += 1

            # Send
            try:
                ok = connect_fn(person.urn, note)
                if ok:
                    ar.executed = True
                    result.executed += 1
                else:
                    ar.error = "connect_fn returned False"
                    result.errors += 1
            except Exception as exc:
                ar.error = str(exc)
                result.errors += 1
                log.warning("Connect failed for %s: %s", person.urn, exc)
            result.results.append(ar)

        return result
