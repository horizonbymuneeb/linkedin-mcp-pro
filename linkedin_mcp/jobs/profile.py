"""User profile + wizard for the Jobs module.

Profile is stored in the existing `session_state` table (same key-value store
used by drafts). The wizard asks a fixed set of clarifying questions and
merges answers on top of the CV-derived defaults.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

# session_state key
_PROFILE_KEY = "jobs_profile_v1"
_CV_KEY = "jobs_cv_parsed_v1"
_SETTINGS_KEY = "jobs_settings_v1"


# ==================== WIZARD QUESTIONS ====================

# Each question is a dict with: id, q, type, options?, required, default
WIZARD_QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "work_mode",
        "q": "Work mode preference?",
        "type": "select",
        "options": [
            {"value": "remote", "label": "Remote only"},
            {"value": "hybrid", "label": "Hybrid (some days in office)"},
            {"value": "onsite", "label": "On-site only"},
            {"value": "any", "label": "Any / no preference"},
        ],
        "default": "remote",
    },
    {
        "id": "cities",
        "q": "Cities you'd consider (comma-separated, leave blank if remote-only or anywhere).",
        "type": "text",
        "placeholder": "e.g. San Francisco, NYC, London, Berlin",
        "default": [],
    },
    {
        "id": "willing_to_relocate",
        "q": "Willing to relocate for the right role?",
        "type": "select",
        "options": [
            {"value": True, "label": "Yes"},
            {"value": False, "label": "No"},
        ],
        "default": False,
    },
    {
        "id": "visa_status",
        "q": "Visa / work authorization?",
        "type": "select",
        "options": [
            {"value": "citizen", "label": "Citizen / permanent resident"},
            {"value": "needs_sponsorship", "label": "Need visa sponsorship"},
            {"value": "unspecified", "label": "Prefer not to say"},
        ],
        "default": "unspecified",
    },
    {
        "id": "min_salary_usd",
        "q": "Minimum base salary (USD/year, leave 0 for any).",
        "type": "number",
        "min": 0,
        "max": 1_000_000,
        "default": 0,
    },
    {
        "id": "role_types",
        "q": "Role types you'd consider (pick any).",
        "type": "multiselect",
        "options": [
            {"value": "ic", "label": "Individual contributor"},
            {"value": "tech_lead", "label": "Tech lead"},
            {"value": "manager", "label": "Engineering manager"},
            {"value": "director", "label": "Director / Sr. manager"},
            {"value": "vp", "label": "VP / Head of"},
            {"value": "founder", "label": "Founder / early-stage"},
        ],
        "default": ["ic"],
    },
    {
        "id": "seniority",
        "q": "Seniority level (pick any).",
        "type": "multiselect",
        "options": [
            {"value": "junior", "label": "Junior"},
            {"value": "mid", "label": "Mid"},
            {"value": "senior", "label": "Senior"},
            {"value": "staff", "label": "Staff / Senior staff"},
            {"value": "principal", "label": "Principal / Distinguished"},
        ],
        "default": ["senior", "staff"],
    },
    {
        "id": "industries",
        "q": "Industries of interest (comma-separated, blank = any).",
        "type": "text",
        "placeholder": "e.g. AI/ML, Fintech, Healthtech, Developer tools",
        "default": [],
    },
    {
        "id": "easy_apply_only",
        "q": "Only apply via LinkedIn Easy Apply?",
        "type": "select",
        "options": [
            {"value": True, "label": "Yes — Easy Apply only"},
            {"value": False, "label": "No — any company site is fine"},
        ],
        "default": True,
    },
    {
        "id": "blacklist_companies",
        "q": "Companies to NEVER apply to (comma-separated).",
        "type": "text",
        "placeholder": "e.g. Meta, Oracle, Bytedance",
        "default": [],
    },
]


# ==================== STORAGE ====================


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _NoDB:
    """Stub DB so module can be imported without a real DB."""

    def transaction(self):
        raise RuntimeError("DB not connected")

    def audit(self, *a, **kw):
        pass


_db: Any = _NoDB()
_db_bind_failed: bool = False


def bind_db(db: Any) -> None:
    """Bind the shared DB instance (call once at app startup)."""
    global _db, _db_bind_failed
    _db = db
    _db_bind_failed = False


def try_lazy_bind() -> None:
    """If a DB is not bound, try to import the project's web._db() and bind it.

    Safe to call multiple times. This is a fallback so the jobs module can be
    used without an explicit bind at app startup.
    """
    global _db, _db_bind_failed
    if not isinstance(_db, _NoDB) or _db_bind_failed:
        return
    try:
        from linkedin_mcp.web import _db as _project_db  # type: ignore

        # project _db is a function; bind a tiny adapter that always returns a fresh DB
        class _LazyAdapter:
            def transaction(self):
                return _project_db().transaction()  # type: ignore[attr-defined]

            def audit(self, *a, **kw):
                return _project_db().audit(*a, **kw)  # type: ignore[attr-defined]

        _db = _LazyAdapter()
    except Exception:
        _db_bind_failed = True


def _load_state(key: str) -> dict[str, Any]:
    try:
        with _db.transaction() as conn:  # type: ignore[attr-defined]
            row = conn.execute("SELECT value FROM session_state WHERE key = ?", (key,)).fetchone()
        if not row or not row[0]:
            return {}
        return json.loads(row[0])
    except Exception:
        return {}


def _save_state(key: str, value: dict[str, Any]) -> None:
    payload = json.dumps(value, ensure_ascii=False, default=str)
    with _db.transaction() as conn:  # type: ignore[attr-defined]
        conn.execute(
            "INSERT OR REPLACE INTO session_state(key, value, updated_at) VALUES (?, ?, ?)",
            (key, payload, _now_iso()),
        )


# ==================== PROFILE API ====================


def get_profile() -> dict[str, Any]:
    return _load_state(_PROFILE_KEY)


def get_cv() -> dict[str, Any]:
    return _load_state(_CV_KEY)


def save_profile(profile: dict[str, Any]) -> dict[str, Any]:
    profile = dict(profile)
    profile["updated_at"] = _now_iso()
    if "created_at" not in profile:
        profile["created_at"] = _now_iso()
    _save_state(_PROFILE_KEY, profile)
    return profile


def save_cv(parsed: dict[str, Any], file_id: str, filename: str) -> dict[str, Any]:
    record = dict(parsed)
    record["file_id"] = file_id
    record["filename"] = filename
    record["uploaded_at"] = _now_iso()
    _save_state(_CV_KEY, record)
    return record


def wizard_complete() -> bool:
    p = get_profile()
    # minimum fields for wizard completion
    required = ["work_mode", "visa_status", "role_types", "seniority"]
    return all(p.get(k) for k in required)


def apply_cv_defaults(cv: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    """Merge CV-extracted fields + wizard answers into a single profile dict."""
    profile: dict[str, Any] = {}
    # From CV
    if cv.get("name"):
        profile["name"] = cv["name"]
    if cv.get("email"):
        profile["email"] = cv["email"]
    if cv.get("phone"):
        profile["phone"] = cv["phone"]
    if cv.get("links"):
        profile["links"] = cv["links"]
    if cv.get("summary"):
        profile["summary"] = cv["summary"]
    if cv.get("skills"):
        profile["skills"] = cv["skills"]
    if cv.get("experience"):
        profile["experience"] = cv["experience"]
    if cv.get("education"):
        profile["education"] = cv["education"]
    # Wizard answers override / fill
    for k, v in answers.items():
        profile[k] = v
    return profile


# ==================== SETTINGS ====================


def get_settings() -> dict[str, Any]:
    return _load_state(_SETTINGS_KEY)


def save_settings(updates: dict[str, Any]) -> dict[str, Any]:
    cur = get_settings()
    cur.update(updates)
    cur["updated_at"] = _now_iso()
    _save_state(_SETTINGS_KEY, cur)
    return cur


def default_settings() -> dict[str, Any]:
    return {
        "enabled": True,
        "dry_run": True,  # always default to dry_run
        "daily_apply_limit": 15,
        "min_match_score": 60,
        "auto_apply_threshold": 85,
        "blacklist_companies": [],
        "use_template": "default",
    }


# ==================== QUESTIONS HELPER ====================


def get_wizard_questions() -> list[dict[str, Any]]:
    return WIZARD_QUESTIONS


def wizard_required_questions() -> list[str]:
    return [q["id"] for q in WIZARD_QUESTIONS if q.get("required")]
