"""Lead scraper for linkedin-mcp-pro (v1.0.0).

NOT IMPLEMENTED as a real scraper (that would require LinkedIn API
access + ban-safety review). Instead, this module provides a
configurable CSV exporter so you can pipe scraped profiles from
any external tool into the workflow.

Profile records have: name, profile_url, title, company, location,
tags, notes. Export as CSV ready for any CRM import.
"""

from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml


class LeadError(Exception):
    """Raised for any lead-list failure."""


@dataclass
class Lead:
    name: str
    profile_url: str
    title: str = ""
    company: str = ""
    location: str = ""
    tags: list[str] = field(default_factory=list)
    notes: str = ""


class LeadScraper:
    """File-backed lead list (YAML storage, CSV export)."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(
            path
            or os.environ.get("LINKEDIN_MCP_LEADS_FILE")
            or (Path.home() / ".linkedin-mcp" / "leads.yaml")
        )

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"leads": []}
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {"leads": []}
        except yaml.YAMLError as e:
            raise LeadError(f"Invalid YAML in {self.path}: {e}") from e

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True, width=120)

    def list_leads(self) -> list[Lead]:
        items = self._read().get("leads", []) or []
        return [
            Lead(
                name=str(l.get("name", "")),
                profile_url=str(l.get("profile_url", "")),
                title=str(l.get("title", "")),
                company=str(l.get("company", "")),
                location=str(l.get("location", "")),
                tags=[str(t) for t in (l.get("tags", []) or [])],
                notes=str(l.get("notes", "")),
            )
            for l in items
        ]

    def add(self, lead: Lead) -> Lead:
        if not lead.name:
            raise LeadError("Lead needs a name")
        if not lead.profile_url:
            raise LeadError(f"Lead {lead.name!r} needs a profile_url")
        current = self.list_leads()
        if any(l.profile_url == lead.profile_url for l in current):
            raise LeadError(f"Lead {lead.profile_url!r} already in list")
        current.append(lead)
        self._write({"leads": [_to_dict(l) for l in current]})
        return lead

    def remove(self, profile_url: str) -> bool:
        current = self.list_leads()
        new = [l for l in current if l.profile_url != profile_url]
        if len(new) == len(current):
            return False
        self._write({"leads": [_to_dict(l) for l in new]})
        return True

    def filter(self, *, tag: Optional[str] = None, company: Optional[str] = None) -> list[Lead]:
        out = self.list_leads()
        if tag:
            out = [l for l in out if tag in l.tags]
        if company:
            out = [l for l in out if company.lower() in l.company.lower()]
        return out

    def to_csv(self, leads: Optional[Iterable[Lead]] = None) -> str:
        """Export leads as CSV (CRM-ready)."""
        items = list(leads) if leads is not None else self.list_leads()
        buf = io.StringIO()
        writer = csv.DictWriter(
            buf,
            fieldnames=["name", "profile_url", "title", "company", "location", "tags", "notes"],
        )
        writer.writeheader()
        for l in items:
            row = asdict(l)
            row["tags"] = ";".join(l.tags)
            writer.writerow(row)
        return buf.getvalue()


def _to_dict(l: Lead) -> dict[str, Any]:
    return {
        "name": l.name,
        "profile_url": l.profile_url,
        "title": l.title,
        "company": l.company,
        "location": l.location,
        "tags": list(l.tags),
        "notes": l.notes,
    }