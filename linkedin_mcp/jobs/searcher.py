"""Job search aggregator.

For now, the LinkedIn source uses the existing `linkedin_mcp.api.jobs` and
`linkedin_mcp.api.search` Voyager clients (cookie-authenticated). If those
aren't connected, returns a small set of stub jobs so the UI is testable.

Other sources (Adzuna, RemoteOK, etc) can be added under `sources/`.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ==================== STUB SOURCE ====================


def _stub_jobs(query: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a deterministic small set of fake jobs so the UI works in dev."""
    kw = (query.get("keywords") or "").lower() or "engineer"
    base = [
        {
            "id": "stub-001",
            "source": "stub",
            "title": f"Senior {kw.title()}",
            "company": "Acme Labs",
            "location": "Remote",
            "remote": True,
            "easy_apply": True,
            "url": "https://www.linkedin.com/jobs/view/000001",
            "description": (
                f"Looking for an experienced {kw} to join our team. "
                "You'll work across the stack, ship fast, and own your work end-to-end. "
                "Stack: Python, TypeScript, PostgreSQL, AWS. "
                "We value autonomy, clear writing, and shipping in small increments."
            ),
            "posted_at": "2026-06-18T10:00:00Z",
            "salary_min": 150000,
            "salary_max": 220000,
        },
        {
            "id": "stub-002",
            "source": "stub",
            "title": f"Staff {kw.title()}",
            "company": "Globex",
            "location": "San Francisco, CA",
            "remote": False,
            "easy_apply": False,
            "url": "https://www.linkedin.com/jobs/view/000002",
            "description": (
                f"Globex is hiring a Staff {kw} to lead a team of 5. "
                "Requirements: 8+ years experience, strong systems design, "
                "experience mentoring senior engineers, comfort with ambiguity. "
                "Hybrid — 3 days in office."
            ),
            "posted_at": "2026-06-17T14:30:00Z",
            "salary_min": 220000,
            "salary_max": 320000,
        },
        {
            "id": "stub-003",
            "source": "stub",
            "title": f"Remote {kw.title()} (Async)",
            "company": "Initech",
            "location": "Remote · EU/US",
            "remote": True,
            "easy_apply": True,
            "url": "https://www.linkedin.com/jobs/view/000003",
            "description": (
                f"Async-first company hiring a {kw}. "
                "Fully remote, 4-day workweek, generous PTO. "
                "Stack: Go, React, Postgres, Kubernetes. "
                "We value written communication and clear thinking."
            ),
            "posted_at": "2026-06-16T09:00:00Z",
            "salary_min": 130000,
            "salary_max": 180000,
        },
        {
            "id": "stub-004",
            "source": "stub",
            "title": f"Junior {kw.title()}",
            "company": "Hooli",
            "location": "Mountain View, CA",
            "remote": False,
            "easy_apply": True,
            "url": "https://www.linkedin.com/jobs/view/000004",
            "description": (
                f"Entry-level {kw} position. "
                "Looking for someone with 0-2 years of experience who's eager to learn. "
                "Mentorship, structured onboarding, and a clear growth path."
            ),
            "posted_at": "2026-06-15T16:00:00Z",
            "salary_min": 90000,
            "salary_max": 130000,
        },
        {
            "id": "stub-005",
            "source": "stub",
            "title": f"Founding {kw.title()}",
            "company": "Pied Piper",
            "location": "Remote · US",
            "remote": True,
            "easy_apply": True,
            "url": "https://www.linkedin.com/jobs/view/000005",
            "description": (
                f"Early-stage startup looking for a founding {kw}. "
                "Significant equity, large scope, work directly with the founders. "
                "Stack: Rust, TypeScript, Postgres. "
                "Comfortable with chaos and wearing many hats."
            ),
            "posted_at": "2026-06-14T11:00:00Z",
            "salary_min": 110000,
            "salary_max": 160000,
        },
    ]
    # Apply simple filters
    out: list[dict[str, Any]] = []
    for j in base:
        if query.get("remote") is True and not j["remote"]:
            continue
        if query.get("easy_apply_only") and not j["easy_apply"]:
            continue
        out.append(j)
    return out[: query.get("max_results", 25)]


# ==================== LINKEDIN SOURCE ====================


def _linkedin_jobs(query: dict[str, Any]) -> Optional[list[dict[str, Any]]]:
    """Try the real Voyager client. Returns None if not authenticated.

    The exact signature of `search_jobs` in linkedin_mcp.api.search is not
    stable across versions, so we try a few common shapes and silently fall
    back to stub if none work.
    """
    try:
        from linkedin_mcp.api.search import search_jobs  # type: ignore
    except Exception as e:
        logger.info("linkedin_mcp.api.search not available: %s", e)
        return None

    raw: Any = None
    for kwargs in (
        # v2 (preferred)
        {
            "keywords": query.get("keywords", ""),
            "location": query.get("location") or "",
            "remote": bool(query.get("remote")),
            "easy_apply": bool(query.get("easy_apply_only")),
            "max_results": int(query.get("max_results", 25)),
        },
        # v1 (legacy)
        {
            "keywords": query.get("keywords", ""),
            "location": query.get("location") or "",
            "limit": int(query.get("max_results", 25)),
        },
        # positional
    ):
        try:
            raw = search_jobs(**kwargs)  # type: ignore[arg-type]
            break
        except TypeError:
            continue
        except Exception as e:
            logger.info("LinkedIn search call failed: %s", e)
            return None
    if not raw:
        return None
    if isinstance(raw, dict):
        raw = raw.get("jobs") or raw.get("results") or raw.get("elements") or []

    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "id": f"li-{item.get('id') or item.get('job_id') or ''}",
                "source": "linkedin",
                "title": item.get("title") or "",
                "company": item.get("company") or item.get("companyName") or "",
                "location": item.get("location") or item.get("formattedLocation") or "",
                "remote": bool(
                    item.get("remote")
                    or "remote" in (item.get("title") or "").lower()
                    or "remote" in (item.get("location") or "").lower()
                ),
                "easy_apply": bool(item.get("easyApply") or item.get("easy_apply")),
                "url": item.get("url") or item.get("jobPostingUrl") or "",
                "description": item.get("description", ""),
                "posted_at": item.get("listedAt") or item.get("posted_at"),
                "salary_min": item.get("salary_min"),
                "salary_max": item.get("salary_max"),
            }
        )
    return out


# ==================== AGGREGATOR ====================


def search(query: dict[str, Any]) -> dict[str, Any]:
    """Run the search across configured sources. Returns {jobs, source_status}."""
    sources = query.get("sources") or ["linkedin"]
    jobs: list[dict[str, Any]] = []
    status: dict[str, str] = {}

    if "linkedin" in sources:
        li = _linkedin_jobs(query)
        if li is None:
            status["linkedin"] = "fallback_stub"
            jobs.extend(_stub_jobs(query))
        else:
            status["linkedin"] = "ok"
            jobs.extend(li)
    else:
        jobs.extend(_stub_jobs(query))
        status["stub"] = "ok"

    # Dedup by (company, title)
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for j in jobs:
        key = (j.get("company", "").lower(), j.get("title", "").lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(j)

    return {"jobs": deduped[: query.get("max_results", 25)], "source_status": status}
