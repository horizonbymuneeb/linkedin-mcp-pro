"""FastAPI router for the Jobs module.

Mounted by web.py as /api/jobs/*
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import time
from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile

from .jobs import applier, cover_letter, cv_parser, matcher, searcher, tracker
from .jobs import profile as prof_mod
from .jobs.schemas import (
    ApplyRequest,
    ApplyResponse,
    ApplicationItem,
    CVUploadResponse,
    JobHit,
    JobsHealth,
    JobsSettingsUpdate,
    ProfileResponse,
    ProfileUpdate,
    SearchRequest,
    SearchResponse,
    TrackerResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

# Lazy bind of the parent DB
_db: Any = None


def bind_db(db: Any) -> None:
    global _db
    _db = db
    prof_mod.bind_db(db)
    tracker.bind_db(db)
    # Make sure profile also has the lazy adapter set up
    prof_mod.try_lazy_bind()


def _audit(action: str, target: str, result: str, detail: dict[str, Any] | None = None) -> None:
    if _db is None:
        return
    try:
        _db.audit(action, target, result, detail=detail or {})  # type: ignore[attr-defined]
    except Exception:
        pass


# ==================== HEALTH ====================


@router.get("/health", response_model=JobsHealth)
def health() -> JobsHealth:
    s = prof_mod.get_settings() or prof_mod.default_settings()
    return JobsHealth(
        ok=True,
        has_cv=bool(prof_mod.get_cv()),
        profile_complete=prof_mod.wizard_complete(),
        settings=s,
        daily_count=tracker.daily_count(),
        daily_limit=int(s.get("daily_apply_limit", 15)),
        sources=[{"name": "linkedin", "kind": "real", "status": "available"}],
        last_search_at=None,
    )


# ==================== CV UPLOAD ====================


@router.post("/cv/upload", response_model=CVUploadResponse)
async def upload_cv(request: Request) -> CVUploadResponse:
    """Accept a multipart CV upload (PDF/DOCX/TXT), parse, and persist."""
    form = await request.form()
    file = form.get("file")
    if file is None:
        raise HTTPException(status_code=400, detail="file is required")
    # starlette UploadFile
    raw = await file.read()
    parsed = cv_parser.parse_cv(file.filename or "cv.pdf", raw)
    file_id = "cv-" + hashlib.sha1(raw).hexdigest()[:16]
    record = prof_mod.save_cv(parsed, file_id=file_id, filename=file.filename or "cv.pdf")
    _audit("cv_upload", file.filename or "cv", "success", {"file_id": file_id, "chars": parsed.get("raw_chars", 0)})
    return CVUploadResponse(
        ok=True,
        file_id=file_id,
        filename=file.filename or "cv.pdf",
        bytes=len(raw),
        text_chars=parsed.get("raw_chars", 0),
        parsed=record,
    )


@router.get("/cv")
def get_cv() -> dict[str, Any]:
    return {"ok": True, "cv": prof_mod.get_cv()}


# ==================== WIZARD ====================


@router.get("/wizard/questions")
def wizard_questions() -> dict[str, Any]:
    return {"ok": True, "questions": prof_mod.get_wizard_questions()}


@router.post("/wizard/submit", response_model=ProfileResponse)
def wizard_submit(answers: dict[str, Any]) -> ProfileResponse:
    """Merge wizard answers with the parsed CV into a single profile."""
    cv = prof_mod.get_cv()
    new_profile = prof_mod.apply_cv_defaults(cv, answers)
    saved = prof_mod.save_profile(new_profile)
    _audit("wizard_submit", "profile", "success", {"fields": list(answers.keys())})
    return ProfileResponse(ok=True, profile=saved, has_cv=bool(cv), wizard_complete=prof_mod.wizard_complete())


# ==================== PROFILE ====================


@router.get("/profile", response_model=ProfileResponse)
def get_profile() -> ProfileResponse:
    p = prof_mod.get_profile()
    return ProfileResponse(ok=True, profile=p, has_cv=bool(prof_mod.get_cv()), wizard_complete=prof_mod.wizard_complete())


@router.put("/profile", response_model=ProfileResponse)
def update_profile(update: ProfileUpdate) -> ProfileResponse:
    p = prof_mod.get_profile()
    for k, v in update.model_dump(exclude_none=True).items():
        p[k] = v
    saved = prof_mod.save_profile(p)
    _audit("profile_update", "self", "success", {"fields": list(update.model_dump(exclude_none=True).keys())})
    return ProfileResponse(ok=True, profile=saved, has_cv=bool(prof_mod.get_cv()), wizard_complete=prof_mod.wizard_complete())


# ==================== SEARCH ====================


@router.post("/search", response_model=SearchResponse)
def search_jobs(req: SearchRequest) -> SearchResponse:
    q = req.model_dump()
    res = searcher.search(q)
    p = prof_mod.get_profile()
    settings = prof_mod.get_settings() or prof_mod.default_settings()
    min_score = int(settings.get("min_match_score", 0))

    jobs: list[JobHit] = []
    for j in res["jobs"]:
        sc = matcher.score_job(p, j)
        score = int(sc.get("score", 0))
        if score < min_score:
            continue
        jobs.append(
            JobHit(
                id=str(j.get("id", "")),
                source=str(j.get("source", "stub")),
                title=str(j.get("title", "")),
                company=str(j.get("company", "")),
                location=j.get("location"),
                remote=bool(j.get("remote", False)),
                easy_apply=bool(j.get("easy_apply", False)),
                url=str(j.get("url", "")),
                description=str(j.get("description", ""))[:1500],
                posted_at=j.get("posted_at"),
                salary_min=j.get("salary_min"),
                salary_max=j.get("salary_max"),
                match_score=score,
                match_reasons=sc.get("reasons", []),
            )
        )
    jobs.sort(key=lambda x: -(x.match_score or 0))
    _audit("job_search", "linkedin", "success", {"query": req.keywords, "results": len(jobs)})
    return SearchResponse(ok=True, jobs=jobs, count=len(jobs), query=q)


# ==================== PREVIEW COVER LETTER (without applying) ====================


@router.post("/cover-letter/preview")
def preview_cover_letter(req: dict[str, Any]) -> dict[str, Any]:
    p = prof_mod.get_profile()
    if not p:
        raise HTTPException(status_code=400, detail="complete the wizard first")
    job = req.get("job") or {}
    tone = req.get("tone", "professional")
    cl = cover_letter.generate(p, job, tone=tone, template=req.get("template", "default"))
    return {"ok": True, "cover_letter": cl}


# ==================== APPLY ====================


@router.post("/apply", response_model=ApplyResponse)
def apply_job(req: ApplyRequest) -> ApplyResponse:
    p = prof_mod.get_profile()
    settings = prof_mod.get_settings() or prof_mod.default_settings()
    # Find job from search results or accept a job dict inline
    job = req.model_dump().get("job") or {"id": req.job_id, "title": "(unknown)", "company": ""}
    res = applier.apply(
        profile_obj=p,
        job=job,
        settings=settings,
        cover_letter_text=req.cover_letter,
        tone=req.tone,
        dry_run=req.dry_run,
    )
    _audit(
        "apply",
        req.job_id,
        "success" if res.get("ok") else "error",
        {"status": res.get("status"), "score": res.get("match_score")},
    )
    return ApplyResponse(
        ok=bool(res.get("ok")),
        application_id=res.get("application_id"),
        status=res.get("status", "failed"),
        detail=res.get("detail", ""),
        cover_letter=res.get("cover_letter"),
        blocked_reason=res.get("blocked_reason"),
    )


# ==================== TRACKER ====================


@router.get("/applications", response_model=TrackerResponse)
def list_applications(status: Optional[str] = Query(None), limit: int = Query(100)) -> TrackerResponse:
    rows = tracker.list_all(status=status, limit=limit)
    items = [
        ApplicationItem(
            id=r.get("id", ""),
            job_id=r.get("job_id", ""),
            job_title=r.get("job_title", ""),
            company=r.get("company", ""),
            status=r.get("status", ""),
            applied_at=r.get("applied_at"),
            match_score=r.get("match_score"),
            cover_letter=r.get("cover_letter"),
            notes=r.get("notes"),
            url=r.get("url"),
        )
        for r in rows
    ]
    return TrackerResponse(ok=True, applications=items, total=len(items), stats=tracker.stats())


@router.patch("/applications/{application_id}")
def patch_application(application_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    status = payload.get("status")
    notes = payload.get("notes")
    if not status:
        raise HTTPException(status_code=400, detail="status is required")
    ok = tracker.update_status(application_id, status, notes=notes)
    if not ok:
        raise HTTPException(status_code=500, detail="update failed")
    _audit("application_status", application_id, "success", {"status": status})
    return {"ok": True}


@router.delete("/applications/{application_id}")
def delete_application(application_id: str) -> dict[str, Any]:
    ok = tracker.delete(application_id)
    return {"ok": ok}


# ==================== SETTINGS ====================


@router.get("/settings")
def get_settings() -> dict[str, Any]:
    s = prof_mod.get_settings() or prof_mod.default_settings()
    return {"ok": True, "settings": s}


@router.put("/settings")
def update_settings(update: JobsSettingsUpdate) -> dict[str, Any]:
    s = prof_mod.save_settings(update.model_dump(exclude_none=True))
    _audit("jobs_settings", "self", "success", {"fields": list(update.model_dump(exclude_none=True).keys())})
    return {"ok": True, "settings": s}


# ==================== TEMPLATES ====================


@router.get("/templates")
def list_templates() -> dict[str, Any]:
    return {"ok": True, "templates": cover_letter.get_templates()}


# ==================== UTILS ====================


@router.post("/reset")
def reset() -> dict[str, Any]:
    """Wipe profile + CV + applications + settings. Returns count cleared."""
    from . import profile as _p

    cleared = 0
    try:
        # delete CV
        cv = _p.get_cv()
        if cv:
            _p.save_cv({"_deleted": True}, file_id="", filename="")  # type: ignore[arg-type]
            cleared += 1
        # delete profile
        if _p.get_profile():
            _p.save_profile({})
            cleared += 1
        # delete settings
        _p.save_settings({})
        cleared += 1
        # applications
        for a in tracker.list_all(limit=1000):
            tracker.delete(a["id"])
            cleared += 1
    except Exception as e:
        logger.warning("reset partial failure: %s", e)
    _audit("jobs_reset", "self", "success", {"cleared": cleared})
    return {"ok": True, "cleared": cleared}
