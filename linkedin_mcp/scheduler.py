"""Post scheduler for linkedin-mcp-pro (v0.5.0).

Schedules live in ``~/.linkedin-mcp/schedule.yaml`` by default
(override with ``LINKEDIN_MCP_SCHEDULE_FILE``). Each schedule declares
*when* to post (cron / specific datetime / weekly day+time) and *what*
to post (template name + variables, or direct text).

The scheduler enqueues ready posts into the existing ``action_queue``
table; the worker (scheduler_worker.py) drains the queue and runs
each post through the standard SafetyGuard + create_post pipeline.

YAML schema::

    schedules:
      - name: monday-motivation
        template: weekly-update          # optional: render a template
        vars:                           # optional: template variables
          topic: deepseek
        text: "Direct text body"        # alternative to template
        cron: "0 9 * * 1"               # 5-field cron (UTC)
        at: "2026-06-20T09:00:00Z"      # one-shot specific datetime
        days: [mon, wed]                # weekly: day names
        time: "09:00"                   # weekly: HH:MM UTC
        tags: [monday]
        enabled: true
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

try:
    from croniter import croniter
except ImportError:  # pragma: no cover - croniter is required at runtime
    croniter = None  # type: ignore[assignment]


VALID_DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class SchedulerError(Exception):
    """Raised for any scheduler-store failure (bad YAML, missing fields, etc.)."""


@dataclass
class Schedule:
    """A single post schedule entry."""

    name: str
    cron: Optional[str] = None
    at: Optional[str] = None
    days: list[str] = field(default_factory=list)
    time: Optional[str] = None
    template: Optional[str] = None
    vars: dict[str, Any] = field(default_factory=dict)
    text: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "enabled": self.enabled}
        if self.cron:
            d["cron"] = self.cron
        if self.at:
            d["at"] = self.at
        if self.days:
            d["days"] = list(self.days)
        if self.time:
            d["time"] = self.time
        if self.template:
            d["template"] = self.template
        if self.vars:
            d["vars"] = dict(self.vars)
        if self.text:
            d["text"] = self.text
        if self.tags:
            d["tags"] = list(self.tags)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Schedule":
        if not isinstance(data, dict):
            raise SchedulerError(
                f"Schedule must be a mapping, got {type(data).__name__}"
            )
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            raise SchedulerError("Schedule is missing required field 'name'")
        cron = data.get("cron")
        at = data.get("at")
        days = data.get("days", []) or []
        time_ = data.get("time")
        template = data.get("template")
        text = data.get("text")
        vars_ = data.get("vars", {}) or {}
        tags = data.get("tags", []) or []
        enabled = bool(data.get("enabled", True))
        if not cron and not at and not (days or time_):
            raise SchedulerError(
                f"Schedule '{name}' needs one of: cron, at, or (days and/or time)"
            )
        if not template and not text:
            raise SchedulerError(
                f"Schedule '{name}' needs either 'template' or 'text'"
            )
        if cron and croniter is None:
            raise SchedulerError(
                "croniter package required for 'cron' schedules. "
                "pip install croniter"
            )
        if cron:
            try:
                croniter(cron)
            except Exception as e:  # croniter raises a wide variety
                raise SchedulerError(
                    f"Schedule '{name}' has invalid cron '{cron}': {e}"
                ) from e
        if at:
            try:
                # accept both 'Z' and '+00:00'
                datetime.fromisoformat(at.replace("Z", "+00:00"))
            except ValueError as e:
                raise SchedulerError(
                    f"Schedule '{name}' has invalid 'at' datetime: {e}"
                ) from e
        if days:
            bad = [d for d in days if d not in VALID_DAYS]
            if bad:
                raise SchedulerError(
                    f"Schedule '{name}' has invalid days {bad}; "
                    f"valid: {sorted(VALID_DAYS)}"
                )
        if time_:
            if not TIME_RE.match(time_):
                raise SchedulerError(
                    f"Schedule '{name}' has invalid 'time' {time_!r}; "
                    f"expected HH:MM (00:00 – 23:59)"
                )
        if not isinstance(vars_, dict):
            raise SchedulerError(
                f"Schedule '{name}' field 'vars' must be a mapping"
            )
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise SchedulerError(
                f"Schedule '{name}' field 'tags' must be a list of strings"
            )
        return cls(
            name=name.strip(),
            cron=cron,
            at=at,
            days=[d.strip().lower() for d in days],
            time=time_,
            template=template,
            vars=dict(vars_),
            text=text,
            tags=list(tags),
            enabled=enabled,
        )


class PostScheduler:
    """File-backed post scheduler.

    Backed by a single YAML file. ``enqueue_due`` materialises the
    schedules into the DB ``action_queue`` table for the worker to drain.
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(
            path
            or os.environ.get("LINKEDIN_MCP_SCHEDULE_FILE")
            or (Path.home() / ".linkedin-mcp" / "schedule.yaml")
        )

    # -- I/O helpers -----------------------------------------------------

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schedules": []}
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {"schedules": []}
        except yaml.YAMLError as e:
            raise SchedulerError(f"Invalid YAML in {self.path}: {e}") from e

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True, width=120)

    # -- CRUD ------------------------------------------------------------

    def list_schedules(self) -> list[Schedule]:
        data = self._read()
        items = data.get("schedules", []) or []
        return [Schedule.from_dict(item) for item in items]

    def get(self, name: str) -> Schedule:
        for s in self.list_schedules():
            if s.name == name:
                return s
        raise SchedulerError(
            f"Schedule {name!r} not found in {self.path}. "
            f"Use 'linkedin-mcp schedule list' to see available names."
        )

    def save_all(self, schedules: Iterable[Schedule]) -> None:
        data = {"schedules": [s.to_dict() for s in schedules]}
        self._write(data)

    def add(self, schedule: Schedule) -> None:
        current = self.list_schedules()
        if any(s.name == schedule.name for s in current):
            raise SchedulerError(
                f"Schedule {schedule.name!r} already exists. "
                f"Use 'enable' or update it instead."
            )
        current.append(schedule)
        self.save_all(current)

    def remove(self, name: str) -> bool:
        current = self.list_schedules()
        new = [s for s in current if s.name != name]
        if len(new) == len(current):
            return False
        self.save_all(new)
        return True

    def _update(self, name: str, **kwargs: Any) -> Schedule:
        current = self.list_schedules()
        for i, s in enumerate(current):
            if s.name == name:
                for k, v in kwargs.items():
                    setattr(s, k, v)
                current[i] = Schedule.from_dict(s.to_dict())
                self.save_all(current)
                return current[i]
        raise SchedulerError(f"Schedule {name!r} not found")

    def enable(self, name: str) -> Schedule:
        return self._update(name, enabled=True)

    def disable(self, name: str) -> Schedule:
        return self._update(name, enabled=False)

    # -- When does it next run? ------------------------------------------

    def next_run(
        self,
        schedule: Schedule,
        now: datetime | None = None,
    ) -> Optional[datetime]:
        """Compute the next run time for a schedule, or None if past (one-shot)."""
        if not schedule.enabled:
            return None
        now = now or datetime.now(timezone.utc)
        if schedule.at:
            at = datetime.fromisoformat(schedule.at.replace("Z", "+00:00"))
            if at <= now:
                return None
            return at
        if schedule.cron and croniter is not None:
            return croniter(schedule.cron, now).get_next(datetime)
        if schedule.days or schedule.time:
            return _next_weekly(schedule, now)
        return None

    # -- Enqueue to DB ---------------------------------------------------

    def enqueue_due(
        self,
        db: Any,
        now: datetime | None = None,
        lookback: timedelta = timedelta(minutes=5),
    ) -> list[int]:
        """Materialise every due schedule into the action_queue.

        Returns the list of queue row IDs created. A one-shot schedule
        whose ``at`` is in the past is enqueued once (then will appear
        in a future ``next_run`` call as ``None`` to avoid re-firing).
        A recurring schedule (cron/weekly) is enqueued only if its next
        run is between ``now - lookback`` and ``now``.
        """
        now = now or datetime.now(timezone.utc)
        ids: list[int] = []
        for s in self.list_schedules():
            if not s.enabled:
                continue
            nxt = self.next_run(s, now=now)
            if s.at:
                # One-shot: enqueue if at is in the past (and recently)
                at = datetime.fromisoformat(s.at.replace("Z", "+00:00"))
                if at > now:
                    continue
                if (now - at) > lookback:
                    continue
                scheduled_at = at
            elif nxt is None:
                continue
            elif nxt > now:
                continue
            elif (now - nxt) > lookback:
                continue
            else:
                scheduled_at = nxt
            payload: dict[str, Any] = {
                "text": s.text,
                "template": s.template,
                "vars": s.vars,
                "schedule_name": s.name,
            }
            if scheduled_at.tzinfo is None:
                scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
            ids.append(
                db.enqueue(
                    "post",
                    payload,
                    scheduled_at=scheduled_at.isoformat(timespec="seconds"),
                )
            )
        return ids


def _next_weekly(schedule: Schedule, now: datetime) -> datetime:
    """Compute the next weekly run for a schedule with days[] and time."""
    target_h, target_m = 9, 0
    if schedule.time:
        h, m = schedule.time.split(":")
        target_h, target_m = int(h), int(m)
    day_offsets = {
        "mon": 0, "tue": 1, "wed": 2, "thu": 3,
        "fri": 4, "sat": 5, "sun": 6,
    }
    valid_days = [day_offsets[d] for d in (schedule.days or list(day_offsets.keys()))]
    for offset in range(0, 8):  # 0 = today, 7 = next week's same day
        candidate_day = (now.weekday() + offset) % 7
        if candidate_day not in valid_days:
            continue
        candidate = (now + timedelta(days=offset)).replace(
            hour=target_h, minute=target_m, second=0, microsecond=0
        )
        if candidate > now:
            return candidate
    # Fallback: 7 days from now at the target time
    return (now + timedelta(days=7)).replace(
        hour=target_h, minute=target_m, second=0, microsecond=0
    )