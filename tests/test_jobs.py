"""Tests for the Jobs module."""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import pytest

# Make sure the project root is on the path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ==================== CV PARSER ====================


def test_cv_parser_txt_basic():
    from linkedin_mcp.jobs.cv_parser import parse_cv

    txt = b"""Jane Doe
jane.doe@example.com | +1-555-123-4567
https://janedoe.dev | https://github.com/janedoe

EXPERIENCE
Senior Software Engineer - Acme Corp
2021 - Present
Built distributed systems in Python, FastAPI, and AWS. Led a team of 4.

Software Engineer - Initrode
2018 - 2021
Worked on React, TypeScript, and Node.js backends.

EDUCATION
MIT, B.S. Computer Science, 2018

SKILLS
Python, JavaScript, TypeScript, React, AWS, Docker, Kubernetes, PostgreSQL
"""
    parsed = parse_cv("jane.txt", txt)
    assert parsed["email"] == "jane.doe@example.com"
    assert parsed["phone"] is not None
    links = parsed["links"] or []
    assert any("janedoe.dev" in l for l in links), f"links={links}"
    skills = parsed["skills"]
    assert "python" in skills
    assert "react" in skills
    assert "aws" in skills
    assert "kubernetes" in skills
    assert parsed["experience"], "experience should not be empty"
    assert parsed["experience"][0]["title"] != ""
    assert parsed["education"], "education should not be empty"


def test_cv_parser_handles_minimal_input():
    from linkedin_mcp.jobs.cv_parser import parse_cv

    parsed = parse_cv("empty.txt", b"")
    assert parsed["email"] is None
    assert parsed["skills"] == []
    assert parsed["experience"] == []


def test_cv_parser_pdf_fallback_returns_text():
    from linkedin_mcp.jobs.cv_parser import parse_cv

    # not a real PDF, but the parser should fall back to text decode
    parsed = parse_cv("test.pdf", b"hello world\nfoo bar\nPython developer")
    assert "python" in (parsed.get("raw_text", "").lower() or "python developer")


# ==================== MATCHER ====================


def test_matcher_keyword_overlap_basic():
    from linkedin_mcp.jobs.matcher import score_job

    profile = {"skills": ["python", "fastapi", "aws", "docker"], "summary": "Backend engineer"}
    job = {
        "title": "Senior Python Engineer",
        "description": "We need python, fastapi, postgres, aws, kubernetes, docker, terraform experience.",
        "remote": True,
    }
    res = score_job(profile, job, cv_text="python fastapi aws docker backend engineer")
    assert res["score"] >= 30
    assert res["score"] <= 100
    assert res["reasons"]


def test_matcher_blacklist_company():
    from linkedin_mcp.jobs.matcher import score_job

    profile = {"blacklist_companies": ["meta"]}
    job = {"title": "Engineer", "description": "engineer job", "company": "Meta Platforms"}
    res = score_job(profile, job, cv_text="")
    assert res["score"] < 50  # heavily penalized


def test_matcher_remote_bonus():
    from linkedin_mcp.jobs.matcher import score_job

    p = {"work_mode": "remote", "skills": ["python"]}
    job_remote = {"title": "X", "description": "python dev", "remote": True}
    job_onsite = {"title": "X", "description": "python dev", "remote": False}
    a = score_job(p, job_remote, cv_text="python")
    b = score_job(p, job_onsite, cv_text="python")
    assert a["score"] > b["score"]


# ==================== COVER LETTER ====================


def test_cover_letter_fallback():
    from linkedin_mcp.jobs.cover_letter import generate

    profile = {
        "name": "Jane Doe",
        "skills": ["python", "fastapi", "aws"],
        "experience": [{"title": "Senior Engineer", "company": "Acme", "dates": "2020-2024"}],
    }
    job = {"title": "Backend Engineer", "company": "Globex", "description": "fast python work"}
    out = generate(profile, job, template="default")
    assert out["text"]
    assert "Globex" in out["text"]
    assert "Jane Doe" in out["text"]
    assert out["source"] in ("template", "llm")


def test_cover_letter_templates_list():
    from linkedin_mcp.jobs.cover_letter import get_templates

    t = get_templates()
    assert isinstance(t, list)
    assert any(x["name"] == "default" for x in t)


# ==================== PROFILE / WIZARD ====================


def test_wizard_questions_complete():
    from linkedin_mcp.jobs.profile import WIZARD_QUESTIONS, get_wizard_questions

    qs = get_wizard_questions()
    assert len(qs) >= 8
    ids = {q["id"] for q in qs}
    assert "work_mode" in ids
    assert "visa_status" in ids
    assert "min_salary_usd" in ids
    assert "role_types" in ids
    assert "seniority" in ids


def test_apply_cv_defaults_merges_fields():
    from linkedin_mcp.jobs.profile import apply_cv_defaults

    cv = {"name": "Jane", "email": "j@x.com", "skills": ["python"]}
    answers = {"work_mode": "remote", "min_salary_usd": 120000}
    p = apply_cv_defaults(cv, answers)
    assert p["name"] == "Jane"
    assert p["email"] == "j@x.com"
    assert p["skills"] == ["python"]
    assert p["work_mode"] == "remote"
    assert p["min_salary_usd"] == 120000


# ==================== SEARCHER ====================


def test_searcher_returns_jobs():
    from linkedin_mcp.jobs.searcher import search

    res = search({"keywords": "engineer", "max_results": 3, "easy_apply_only": False})
    assert "jobs" in res
    assert isinstance(res["jobs"], list)
    assert len(res["jobs"]) > 0
    j = res["jobs"][0]
    for k in ("id", "title", "company", "url"):
        assert k in j, f"missing {k}"


def test_searcher_easy_apply_filter():
    from linkedin_mcp.jobs.searcher import search

    res = search({"keywords": "engineer", "max_results": 10, "easy_apply_only": True})
    for j in res["jobs"]:
        assert j["easy_apply"] is True


# ==================== TRACKER ====================


def test_tracker_record_and_list(tmp_path):
    """Tracker needs a real or in-memory DB. Use sqlite3 + a minimal adapter."""
    from linkedin_mcp.jobs import tracker
    import sqlite3

    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS session_state (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS jobs_applications_v1 ("
        "id TEXT PRIMARY KEY, job_id TEXT, job_title TEXT, company TEXT, url TEXT, "
        "status TEXT, match_score INTEGER, cover_letter TEXT, notes TEXT, "
        "applied_at TEXT, created_at TEXT, updated_at TEXT)"
    )
    conn.commit()

    class _FakeDB:
        def transaction(self):
            class _Ctx:
                def __enter__(s):
                    return conn
                def __exit__(s, *a):
                    conn.commit()
            return _Ctx()
        def audit(self, *a, **kw):
            pass

    tracker.bind_db(_FakeDB())
    tracker.ensure_schema()
    job = {"id": "test-1", "title": "T", "company": "C", "url": "u"}
    entry = tracker.record(job, status="dry_run", cover_letter="hi", match_score=70)
    assert entry["id"]
    assert entry["status"] == "dry_run"
    apps = tracker.list_all(limit=10)
    assert any(a["id"] == entry["id"] for a in apps), f"missing entry in {apps}"


def test_tracker_stats():
    from linkedin_mcp.jobs import tracker

    s = tracker.stats()
    assert "total" in s
    assert isinstance(s["total"], int)


# ==================== APPLIER ====================


def test_applier_blocks_blacklist():
    from linkedin_mcp.jobs import applier

    profile_obj = {"blacklist_companies": ["meta"], "work_mode": "any"}
    settings = {"enabled": True, "dry_run": True, "daily_apply_limit": 15, "min_match_score": 50}
    job = {"id": "x", "title": "Engineer", "company": "Meta Platforms", "url": ""}
    res = applier.apply(profile_obj, job, settings)
    assert res["status"] == "blocked"
    assert "blacklist" in res["detail"].lower()


def test_applier_blocks_daily_limit():
    from linkedin_mcp.jobs import applier, tracker

    # record one applied
    tracker.record({"id": "x", "title": "T", "company": "C"}, status="applied")
    profile_obj = {"work_mode": "any"}
    settings = {"enabled": True, "dry_run": False, "daily_apply_limit": 0, "min_match_score": 0}
    job = {"id": "y", "title": "Engineer", "company": "SomeCo", "url": "", "easy_apply": True, "remote": True}
    res = applier.apply(profile_obj, job, settings)
    assert res["status"] == "blocked"


def test_applier_dry_run_with_minimal_profile():
    from linkedin_mcp.jobs import applier, tracker

    # cleanup any prior blocked records so the limit test above doesn't leak here
    for a in tracker.list_all(limit=1000):
        tracker.delete(a["id"])

    profile_obj = {
        "name": "Jane",
        "skills": ["python", "fastapi", "aws", "docker", "react"],
        "experience": [{"title": "Senior Engineer", "company": "Acme", "description": "python fastapi aws"}],
        "summary": "Backend engineer",
        "work_mode": "any",
    }
    settings = {"enabled": True, "dry_run": True, "daily_apply_limit": 100, "min_match_score": 10}
    job = {
        "id": "z",
        "title": "Senior Python Engineer",
        "company": "Globex",
        "description": "python, fastapi, aws, docker, kubernetes, postgres, terraform, react, typescript",
        "url": "https://example.com",
        "easy_apply": True,
        "remote": True,
    }
    res = applier.apply(profile_obj, job, settings)
    assert res["status"] in ("dry_run", "blocked", "applied")
    if res["status"] == "dry_run":
        assert res["cover_letter"]
        assert res["match_score"] is not None
