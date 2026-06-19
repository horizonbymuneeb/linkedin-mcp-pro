"""Content calendar for linkedin-mcp-pro (v1.0.0).

Lightweight month-view of planned + posted content. Backed by YAML,
each entry has: date, status (idea/drafted/scheduled/posted), text,
tags, post_id (if posted).

This is a planning tool, not a scheduler. Combine with
``linkedin_mcp.scheduler`` to actually fire the posts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml


VALID_STATUSES = {"idea", "drafted", "scheduled", "posted"}


class CalendarError(Exception):
    """Raised for any calendar failure."""


@dataclass
class Entry:
    """One calendar entry."""

    date: str  # YYYY-MM-DD
    status: str = "idea"
    title: str = ""
    body: str = ""
    tags: list[str] = field(default_factory=list)
    post_id: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "status": self.status,
            "title": self.title,
            "body": self.body,
            "tags": list(self.tags),
            "post_id": self.post_id,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Entry":
        if not data.get("date"):
            raise CalendarError("Entry needs a date (YYYY-MM-DD)")
        status = data.get("status", "idea")
        if status not in VALID_STATUSES:
            raise CalendarError(
                f"Invalid status {status!r}; valid: {sorted(VALID_STATUSES)}"
            )
        return cls(
            date=str(data["date"]),
            status=status,
            title=str(data.get("title", "")),
            body=str(data.get("body", "")),
            tags=[str(t) for t in (data.get("tags", []) or [])],
            post_id=str(data.get("post_id", "")),
            notes=str(data.get("notes", "")),
        )


class ContentCalendar:
    """YAML-backed content calendar."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(
            path
            or os.environ.get("LINKEDIN_MCP_CALENDAR_FILE")
            or (Path.home() / ".linkedin-mcp" / "calendar.yaml")
        )

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"entries": []}
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {"entries": []}
        except yaml.YAMLError as e:
            raise CalendarError(f"Invalid YAML in {self.path}: {e}") from e

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True, width=120)

    def list_entries(
        self,
        month: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[Entry]:
        items = self._read().get("entries", []) or []
        if month is not None:
            items = [e for e in items if e.get("date", "").startswith(month)]
        if status is not None:
            items = [e for e in items if e.get("status") == status]
        return [Entry.from_dict(e) for e in items]

    def add(self, entry: Entry) -> Entry:
        # Reject duplicate (date + title)
        current = self.list_entries()
        if any(e.date == entry.date and e.title == entry.title for e in current):
            raise CalendarError(
                f"Entry already exists on {entry.date} with title {entry.title!r}"
            )
        current.append(entry)
        self._write({"entries": [e.to_dict() for e in current]})
        return entry

    def update_status(self, date: str, title: str, new_status: str) -> Entry:
        if new_status not in VALID_STATUSES:
            raise CalendarError(f"Invalid status {new_status!r}")
        current = self.list_entries()
        for i, e in enumerate(current):
            if e.date == date and e.title == title:
                e.status = new_status
                current[i] = e
                self._write({"entries": [e.to_dict() for e in current]})
                return e
        raise CalendarError(f"No entry on {date} with title {title!r}")

    def remove(self, date: str, title: str) -> bool:
        current = self.list_entries()
        new = [e for e in current if not (e.date == date and e.title == title)]
        if len(new) == len(current):
            return False
        self._write({"entries": [e.to_dict() for e in new]})
        return True

    def month_summary(self, month: str) -> dict[str, Any]:
        entries = self.list_entries(month=month)
        by_status: dict[str, int] = {}
        for e in entries:
            by_status[e.status] = by_status.get(e.status, 0) + 1
        return {
            "month": month,
            "total": len(entries),
            "by_status": by_status,
            "entries": [e.to_dict() for e in sorted(entries, key=lambda x: x.date)],
        }