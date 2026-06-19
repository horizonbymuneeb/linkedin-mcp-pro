"""Job application orchestrator.

Pipeline:
  1. Eligibility checks (rate limit, blacklist, easy-apply, score)
  2. Match score (uses matcher.score_job)
  3. Cover letter generation (uses cover_letter.generate)
  4. Apply via the real Easy-Apply flow (agent-browser) OR dry-run
  5. Record in tracker

If the LinkedIn Voyager client isn't authenticated OR the user has
dry_run=True (default), we never actually click Apply — we just record a
"dry_run" entry and return the cover letter.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# keep all references to *modules* so test stubs can monkeypatch
from . import cover_letter, matcher, profile, tracker


def _score(profile_obj: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    cv = profile.get_cv() or {}
    # Build a CV text blob for the matcher
    parts: list[str] = []
    for k in ("summary", "headline"):
        if profile_obj.get(k):
            parts.append(str(profile_obj[k]))
    for s in profile_obj.get("skills") or []:
        parts.append(str(s))
    for e in profile_obj.get("experience") or []:
        parts.append(str(e.get("title") or ""))
        parts.append(str(e.get("description") or ""))
    cv_text = " ".join(parts)
    return matcher.score_job(profile_obj, job, cv_text=cv_text)


def _eligibility(profile_obj: dict[str, Any], settings: dict[str, Any], job: dict[str, Any]) -> tuple[bool, str]:
    if not settings.get("enabled", True):
        return False, "auto-apply is disabled in settings"
    blacklist = [c.lower() for c in (profile_obj.get("blacklist_companies") or []) + (settings.get("blacklist_companies") or [])]
    if blacklist:
        company_lc = (job.get("company") or "").lower()
        if any(b in company_lc for b in blacklist):
            return False, f"company '{job.get('company')}' is on your blacklist"
    daily_limit = int(settings.get("daily_apply_limit", 15) or 15)
    if tracker.daily_count() >= daily_limit:
        return False, f"daily apply limit reached ({daily_limit})"
    if profile_obj.get("easy_apply_only") and not job.get("easy_apply"):
        return False, "job is not Easy Apply and you prefer Easy Apply only"
    work_mode = profile_obj.get("work_mode")
    if work_mode == "remote" and not job.get("remote"):
        return False, "job is not remote and you prefer remote"
    return True, ""


def _browse_apply(job: dict[str, Any], cover_text: str) -> dict[str, Any]:
    """Open LinkedIn Easy Apply and submit. Returns {ok, error, url}."""
    # Real implementation would call agent-browser / patchright
    try:
        from linkedin_mcp.apply import apply_easy  # type: ignore

        return apply_easy(url=job.get("url", ""), cover_letter=cover_text)  # type: ignore[call-arg]
    except Exception as e:
        logger.info("real apply not available, dry-run only: %s", e)
        return {"ok": False, "error": "real apply not wired in this build", "dry_run": True}


def apply(
    profile_obj: dict[str, Any],
    job: dict[str, Any],
    settings: dict[str, Any],
    cover_letter_text: str | None = None,
    tone: str = "professional",
    dry_run: bool | None = None,
) -> dict[str, Any]:
    """Run the apply pipeline. Returns a dict (see ApplyResponse)."""
    if dry_run is None:
        dry_run = bool(settings.get("dry_run", True))

    # 1. Eligibility
    ok, reason = _eligibility(profile_obj, settings, job)
    if not ok:
        entry = tracker.record(job, status="blocked", notes=reason)
        return {"ok": False, "status": "blocked", "detail": reason, "application_id": entry.get("id")}

    # 2. Match
    sc = _score(profile_obj, job)
    score = int(sc.get("score", 0))
    min_score = int(settings.get("min_match_score", 60) or 60)
    if score < min_score:
        entry = tracker.record(job, status="blocked", match_score=score, notes=f"match score {score} < {min_score}")
        return {
            "ok": False,
            "status": "blocked",
            "detail": f"match score {score} below threshold {min_score}",
            "match_score": score,
            "reasons": sc.get("reasons", []),
            "application_id": entry.get("id"),
        }

    # 3. Cover letter
    if cover_letter_text:
        cl_text = cover_letter_text
        cl_meta: dict[str, Any] = {"source": "user-supplied", "model": None}
    else:
        cl = cover_letter.generate(profile_obj, job, tone=tone, template=settings.get("use_template", "default"))
        cl_text = cl.get("text", "")
        cl_meta = {"source": cl.get("source"), "model": cl.get("model")}

    # 4. Apply (real or dry)
    if dry_run:
        entry = tracker.record(job, status="dry_run", cover_letter=cl_text, match_score=score, notes="dry run — not submitted")
        return {
            "ok": True,
            "status": "dry_run",
            "application_id": entry.get("id"),
            "detail": "dry run — would have submitted",
            "cover_letter": cl_text,
            "match_score": score,
            "reasons": sc.get("reasons", []),
            "cover_meta": cl_meta,
        }

    result = _browse_apply(job, cl_text)
    if result.get("ok"):
        entry = tracker.record(job, status="applied", cover_letter=cl_text, match_score=score, notes="applied")
        return {
            "ok": True,
            "status": "applied",
            "application_id": entry.get("id"),
            "detail": "submitted to LinkedIn",
            "cover_letter": cl_text,
            "match_score": score,
            "reasons": sc.get("reasons", []),
            "cover_meta": cl_meta,
        }
    else:
        entry = tracker.record(
            job, status="failed", cover_letter=cl_text, match_score=score, notes=result.get("error", "apply failed")
        )
        return {
            "ok": False,
            "status": "failed",
            "application_id": entry.get("id"),
            "detail": result.get("error", "apply failed"),
            "cover_letter": cl_text,
            "match_score": score,
            "reasons": sc.get("reasons", []),
            "cover_meta": cl_meta,
        }
