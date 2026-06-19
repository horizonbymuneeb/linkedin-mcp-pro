"""Tests for calendar + lead scraper (v1.0.0)."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from linkedin_mcp.calendar import CalendarError, ContentCalendar, Entry
from linkedin_mcp.leads import Lead, LeadError, LeadScraper


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


@pytest.fixture
def cal(tmp_path: Path) -> ContentCalendar:
    return ContentCalendar(tmp_path / "cal.yaml")


def test_empty_calendar(cal: ContentCalendar) -> None:
    assert cal.list_entries() == []


def test_add_and_list(cal: ContentCalendar) -> None:
    e = Entry(date="2026-07-01", title="Launch", body="...", status="idea")
    cal.add(e)
    assert cal.list_entries()[0].title == "Launch"


def test_duplicate_rejected(cal: ContentCalendar) -> None:
    cal.add(Entry(date="2026-07-01", title="x"))
    with pytest.raises(CalendarError):
        cal.add(Entry(date="2026-07-01", title="x"))


def test_update_status(cal: ContentCalendar) -> None:
    cal.add(Entry(date="2026-07-01", title="x"))
    cal.update_status("2026-07-01", "x", "drafted")
    assert cal.list_entries()[0].status == "drafted"


def test_update_status_unknown(cal: ContentCalendar) -> None:
    with pytest.raises(CalendarError):
        cal.update_status("2026-07-01", "ghost", "drafted")


def test_invalid_status_raises(cal: ContentCalendar) -> None:
    with pytest.raises(CalendarError):
        cal.update_status("2026-07-01", "x", "bogus")


def test_remove(cal: ContentCalendar) -> None:
    cal.add(Entry(date="2026-07-01", title="x"))
    assert cal.remove("2026-07-01", "x") is True
    assert cal.remove("2026-07-01", "x") is False


def test_filter_by_month(cal: ContentCalendar) -> None:
    cal.add(Entry(date="2026-07-01", title="a"))
    cal.add(Entry(date="2026-07-15", title="b"))
    cal.add(Entry(date="2026-08-01", title="c"))
    july = cal.list_entries(month="2026-07")
    assert len(july) == 2


def test_filter_by_status(cal: ContentCalendar) -> None:
    cal.add(Entry(date="2026-07-01", title="a", status="idea"))
    cal.add(Entry(date="2026-07-02", title="b", status="posted"))
    posted = cal.list_entries(status="posted")
    assert len(posted) == 1


def test_month_summary(cal: ContentCalendar) -> None:
    cal.add(Entry(date="2026-07-01", title="a", status="idea"))
    cal.add(Entry(date="2026-07-02", title="b", status="posted"))
    cal.add(Entry(date="2026-07-03", title="c", status="posted"))
    s = cal.month_summary("2026-07")
    assert s["total"] == 3
    assert s["by_status"]["posted"] == 2


def test_entry_from_dict_invalid_status() -> None:
    with pytest.raises(CalendarError):
        Entry.from_dict({"date": "2026-07-01", "status": "bogus"})


def test_entry_from_dict_missing_date() -> None:
    with pytest.raises(CalendarError):
        Entry.from_dict({"title": "x"})


# ---------------------------------------------------------------------------
# Lead scraper
# ---------------------------------------------------------------------------


@pytest.fixture
def ls(tmp_path: Path) -> LeadScraper:
    return LeadScraper(tmp_path / "leads.yaml")


def test_empty(ls: LeadScraper) -> None:
    assert ls.list_leads() == []


def test_add_lead(ls: LeadScraper) -> None:
    lead = Lead(name="Alice", profile_url="https://linkedin.com/in/alice", title="Engineer")
    ls.add(lead)
    assert ls.list_leads()[0].name == "Alice"


def test_add_duplicate_profile_url_raises(ls: LeadScraper) -> None:
    ls.add(Lead(name="Alice", profile_url="https://x.com"))
    with pytest.raises(LeadError):
        ls.add(Lead(name="Alice2", profile_url="https://x.com"))


def test_add_requires_name(ls: LeadScraper) -> None:
    with pytest.raises(LeadError):
        ls.add(Lead(name="", profile_url="https://x.com"))


def test_add_requires_url(ls: LeadScraper) -> None:
    with pytest.raises(LeadError):
        ls.add(Lead(name="x", profile_url=""))


def test_remove(ls: LeadScraper) -> None:
    ls.add(Lead(name="A", profile_url="https://x.com"))
    assert ls.remove("https://x.com") is True
    assert ls.remove("https://x.com") is False


def test_filter_by_tag(ls: LeadScraper) -> None:
    ls.add(Lead(name="A", profile_url="https://x.com/1", tags=["vip"]))
    ls.add(Lead(name="B", profile_url="https://x.com/2", tags=["lead"]))
    assert len(ls.filter(tag="vip")) == 1


def test_filter_by_company(ls: LeadScraper) -> None:
    ls.add(Lead(name="A", profile_url="https://x.com/1", company="Acme"))
    ls.add(Lead(name="B", profile_url="https://x.com/2", company="Globex"))
    assert len(ls.filter(company="acme")) == 1


def test_csv_export(ls: LeadScraper) -> None:
    ls.add(Lead(name="A", profile_url="https://x.com", title="Eng", company="Acme", tags=["vip"]))
    csv_str = ls.to_csv()
    rows = list(csv.DictReader(io.StringIO(csv_str)))
    assert len(rows) == 1
    assert rows[0]["name"] == "A"
    assert rows[0]["tags"] == "vip"


def test_csv_export_empty(ls: LeadScraper) -> None:
    csv_str = ls.to_csv()
    rows = list(csv.DictReader(io.StringIO(csv_str)))
    assert rows == []