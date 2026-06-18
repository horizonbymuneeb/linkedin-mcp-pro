"""MCP tools for the post scheduler."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..config import load_config
from ..db import DB
from ..scheduler import PostScheduler, Schedule, SchedulerError


def _sched() -> PostScheduler:
    return PostScheduler()


def _db() -> DB:
    try:
        cfg = load_config()
        return DB(cfg.storage.db_path)
    except Exception:
        return DB(Path("./data/linkedin-mcp-pro.db"))


def list_schedules() -> list[dict[str, Any]]:
    return [s.to_dict() for s in _sched().list_schedules()]


def add_schedule(
    name: str,
    cron: Optional[str] = None,
    at: Optional[str] = None,
    days: Optional[list[str]] = None,
    time: Optional[str] = None,
    template: Optional[str] = None,
    text: Optional[str] = None,
    vars: Optional[dict[str, Any]] = None,
    tags: Optional[list[str]] = None,
) -> dict[str, Any]:
    try:
        sc = Schedule.from_dict(
            {
                "name": name,
                "cron": cron,
                "at": at,
                "days": days or [],
                "time": time,
                "template": template,
                "text": text,
                "vars": vars or {},
                "tags": tags or [],
                "enabled": True,
            }
        )
        _sched().add(sc)
        return {"ok": True, "schedule": sc.to_dict()}
    except SchedulerError as e:
        raise ValueError(str(e)) from e


def remove_schedule(name: str) -> dict[str, Any]:
    ok = _sched().remove(name)
    if not ok:
        raise ValueError(f"Schedule {name!r} not found")
    return {"ok": True, "removed": name}


def enable_schedule(name: str) -> dict[str, Any]:
    try:
        sc = _sched().enable(name)
    except SchedulerError as e:
        raise ValueError(str(e)) from e
    return {"ok": True, "schedule": sc.to_dict()}


def disable_schedule(name: str) -> dict[str, Any]:
    try:
        sc = _sched().disable(name)
    except SchedulerError as e:
        raise ValueError(str(e)) from e
    return {"ok": True, "schedule": sc.to_dict()}


def run_due_now() -> dict[str, Any]:
    db = _db()
    ids = _sched().enqueue_due(db)
    return {"ok": True, "enqueued": len(ids), "queue_ids": ids}