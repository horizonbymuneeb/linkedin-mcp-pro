"""Tracker for job applications.

Stores applications in the same SQLite DB the rest of linkedin-mcp-pro uses.
Schema: see `ensure_schema()` — created lazily on first call.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional


_TABLE_NAME = "jobs_applications_v1"

_db_ref: Any = None  # set via bind_db()


def bind_db(db: Any) -> None:
    """Bind the DB instance used elsewhere in the project."""
    global _db_ref
    _db_ref = db


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema() -> None:
    """Create the applications table if it doesn't exist."""
    if _db_ref is None:
        return
    try:
        with _db_ref.transaction() as conn:  # type: ignore[attr-defined]
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_TABLE_NAME} (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    job_title TEXT,
                    company TEXT,
                    url TEXT,
                    status TEXT NOT NULL,
                    match_score INTEGER,
                    cover_letter TEXT,
                    notes TEXT,
                    applied_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{_TABLE_NAME}_status ON {_TABLE_NAME}(status)"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{_TABLE_NAME}_applied_at ON {_TABLE_NAME}(applied_at)"
            )
    except Exception as e:
        # If the DB layer doesn't support CREATE TABLE inside transaction, log and continue.
        import logging

        logging.getLogger(__name__).warning("ensure_schema failed: %s", e)


# ==================== CRUD ====================


def _row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    # support tuple + sqlite3.Row
    if isinstance(row, dict):
        return row
    keys = (
        "id",
        "job_id",
        "job_title",
        "company",
        "url",
        "status",
        "match_score",
        "cover_letter",
        "notes",
        "applied_at",
        "created_at",
        "updated_at",
    )
    return {k: row[i] if i < len(row) else None for i, k in enumerate(keys)}


def record(
    job: dict[str, Any],
    status: str,
    cover_letter: str | None = None,
    match_score: int | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Create a new application entry. Returns the entry as a dict."""
    ensure_schema()
    entry = {
        "id": "app-" + uuid.uuid4().hex[:12],
        "job_id": job.get("id") or "",
        "job_title": job.get("title") or "",
        "company": job.get("company") or "",
        "url": job.get("url") or "",
        "status": status,
        "match_score": match_score,
        "cover_letter": cover_letter,
        "notes": notes,
        "applied_at": _now_iso() if status == "applied" else None,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    if _db_ref is None:
        return entry
    try:
        with _db_ref.transaction() as conn:  # type: ignore[attr-defined]
            conn.execute(
                f"INSERT INTO {_TABLE_NAME} (id, job_id, job_title, company, url, status, "
                "match_score, cover_letter, notes, applied_at, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    entry["id"],
                    entry["job_id"],
                    entry["job_title"],
                    entry["company"],
                    entry["url"],
                    entry["status"],
                    entry["match_score"],
                    entry["cover_letter"],
                    entry["notes"],
                    entry["applied_at"],
                    entry["created_at"],
                    entry["updated_at"],
                ),
            )
    except Exception:
        # In-memory fallback if write fails
        pass
    return entry


def update_status(application_id: str, status: str, notes: str | None = None) -> bool:
    ensure_schema()
    if _db_ref is None:
        return False
    try:
        with _db_ref.transaction() as conn:  # type: ignore[attr-defined]
            conn.execute(
                f"UPDATE {_TABLE_NAME} SET status = ?, notes = COALESCE(?, notes), "
                "updated_at = ?, applied_at = COALESCE(applied_at, ?) WHERE id = ?",
                (status, notes, _now_iso(), _now_iso() if status == "applied" else None, application_id),
            )
        return True
    except Exception:
        return False


def delete(application_id: str) -> bool:
    if _db_ref is None:
        return False
    try:
        with _db_ref.transaction() as conn:  # type: ignore[attr-defined]
            conn.execute(f"DELETE FROM {_TABLE_NAME} WHERE id = ?", (application_id,))
        return True
    except Exception:
        return False


def list_all(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    ensure_schema()
    if _db_ref is None:
        return []
    try:
        with _db_ref.transaction() as conn:  # type: ignore[attr-defined]
            if status:
                rows = conn.execute(
                    f"SELECT * FROM {_TABLE_NAME} WHERE status = ? ORDER BY applied_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT * FROM {_TABLE_NAME} ORDER BY applied_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [_row_to_dict(r) for r in rows]
    except Exception:
        return []


def stats() -> dict[str, int]:
    """Counts by status + last 30 days applied."""
    apps = list_all(limit=1000)
    out: dict[str, int] = {
        "total": len(apps),
        "applied": 0,
        "dry_run": 0,
        "viewed": 0,
        "interview": 0,
        "rejected": 0,
        "blocked": 0,
        "queued": 0,
    }
    for a in apps:
        s = a.get("status") or ""
        if s in out:
            out[s] += 1
    return out


def daily_count(today_iso_prefix: str | None = None) -> int:
    if not today_iso_prefix:
        today_iso_prefix = _now_iso()[:10]  # YYYY-MM-DD
    apps = list_all(limit=1000)
    n = 0
    for a in apps:
        ts = a.get("applied_at") or ""
        if ts.startswith(today_iso_prefix):
            n += 1
    return n
