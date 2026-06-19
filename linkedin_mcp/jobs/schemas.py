"""Pydantic schemas for the Jobs module."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ==================== CV / Profile ====================


class CVUploadResponse(BaseModel):
    ok: bool = True
    file_id: str
    filename: str
    bytes: int
    text_chars: int
    parsed: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured fields extracted from the CV (skills, experience, education, summary).",
    )


class ProfileUpdate(BaseModel):
    """Body for PUT /api/jobs/profile — partial update."""

    name: Optional[str] = None
    headline: Optional[str] = None
    summary: Optional[str] = None
    location: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    skills: Optional[list[str]] = None
    experience: Optional[list[dict[str, Any]]] = None
    education: Optional[list[dict[str, Any]]] = None
    # preferences
    work_mode: Optional[str] = None  # "remote" | "onsite" | "hybrid"
    cities: Optional[list[str]] = None
    willing_to_relocate: Optional[bool] = None
    visa_status: Optional[str] = None  # "citizen" | "permanent" | "needs_sponsorship" | "unspecified"
    min_salary_usd: Optional[int] = None
    role_types: Optional[list[str]] = None  # ["ic", "manager", "director", "vp", "c-level"]
    seniority: Optional[list[str]] = None  # ["junior", "mid", "senior", "staff", "principal"]
    industries: Optional[list[str]] = None
    blacklist_companies: Optional[list[str]] = None
    min_match_score: Optional[int] = Field(default=None, ge=0, le=100)
    daily_apply_limit: Optional[int] = Field(default=None, ge=1, le=200)


class ProfileResponse(BaseModel):
    ok: bool = True
    profile: dict[str, Any]
    has_cv: bool
    wizard_complete: bool


# ==================== Search ====================


class SearchRequest(BaseModel):
    keywords: str = Field(..., min_length=1, max_length=200)
    location: Optional[str] = None
    remote: Optional[bool] = None
    easy_apply_only: bool = True
    max_results: int = Field(default=25, ge=1, le=100)
    posted_within_days: int = Field(default=7, ge=1, le=60)
    sources: Optional[list[str]] = None  # ["linkedin"] default


class JobHit(BaseModel):
    id: str
    source: str
    title: str
    company: str
    location: Optional[str] = None
    remote: bool = False
    easy_apply: bool = False
    url: str
    description: str = ""
    posted_at: Optional[str] = None
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    match_score: Optional[int] = None  # 0-100
    match_reasons: Optional[list[str]] = None


class SearchResponse(BaseModel):
    ok: bool = True
    jobs: list[JobHit]
    count: int
    query: dict[str, Any]


# ==================== Apply ====================


class ApplyRequest(BaseModel):
    job_id: str
    cover_letter: Optional[str] = None  # if missing, generate
    tone: str = "professional"  # professional | friendly | concise | enthusiastic
    dry_run: bool = True


class ApplyResponse(BaseModel):
    ok: bool
    application_id: Optional[str] = None
    status: str  # "applied" | "dry_run" | "blocked" | "failed" | "queued"
    detail: str = ""
    cover_letter: Optional[str] = None
    blocked_reason: Optional[str] = None


# ==================== Tracker ====================


class ApplicationItem(BaseModel):
    id: str
    job_id: str
    job_title: str
    company: str
    status: str
    applied_at: Optional[str] = None
    match_score: Optional[int] = None
    cover_letter: Optional[str] = None
    notes: Optional[str] = None
    url: Optional[str] = None


class TrackerResponse(BaseModel):
    ok: bool = True
    applications: list[ApplicationItem]
    total: int
    stats: dict[str, int]


# ==================== Settings ====================


class JobsSettingsUpdate(BaseModel):
    enabled: Optional[bool] = None
    dry_run: Optional[bool] = None
    daily_apply_limit: Optional[int] = Field(default=None, ge=1, le=200)
    min_match_score: Optional[int] = Field(default=None, ge=0, le=100)
    auto_apply_threshold: Optional[int] = Field(default=None, ge=0, le=100)
    blacklist_companies: Optional[list[str]] = None
    use_template: Optional[str] = None  # name of saved cover letter template


# ==================== Health ====================


class JobsHealth(BaseModel):
    ok: bool
    has_cv: bool
    profile_complete: bool
    settings: dict[str, Any]
    daily_count: int
    daily_limit: int
    sources: list[dict[str, Any]]
    last_search_at: Optional[str] = None
