"""Tests for the post scheduler (v0.5.0)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from linkedin_mcp.scheduler import (
    PostScheduler,
    Schedule,
    SchedulerError,
)


FIXED_NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def sched_path(tmp_path: Path) -> Path:
    return tmp_path / "schedule.yaml"


@pytest.fixture
def sched(sched_path: Path) -> PostScheduler:
    return PostScheduler(sched_path)


# ---------------------------------------------------------------------------
# YAML / Schedule parsing
# ---------------------------------------------------------------------------


def test_load_empty_yaml(sched: PostScheduler) -> None:
    assert sched.list_schedules() == []


def test_load_with_schedules(sched: PostScheduler, sched_path: Path) -> None:
    sched_path.write_text(
        yaml.safe_dump(
            {
                "schedules": [
                    {
                        "name": "morning",
                        "cron": "0 9 * * *",
                        "text": "good morning",
                        "enabled": True,
                    }
                ]
            }
        )
    )
    schedules = sched.list_schedules()
    assert len(schedules) == 1
    assert schedules[0].name == "morning"
    assert schedules[0].cron == "0 9 * * *"


def test_save_yaml_preserves_format(sched: PostScheduler, sched_path: Path) -> None:
    s = Schedule(name="weekly", days=["mon", "wed"], time="09:00", text="hi")
    sched.add(s)
    raw = yaml.safe_load(sched_path.read_text())
    assert "schedules" in raw
    assert raw["schedules"][0]["name"] == "weekly"
    assert raw["schedules"][0]["days"] == ["mon", "wed"]


def test_invalid_cron_raises() -> None:
    with pytest.raises(SchedulerError) as ei:
        Schedule.from_dict({"name": "x", "cron": "not a cron", "text": "t"})
    assert "cron" in str(ei.value).lower()


def test_invalid_time_format_raises() -> None:
    with pytest.raises(SchedulerError) as ei:
        Schedule.from_dict({"name": "x", "days": ["mon"], "time": "25:99", "text": "t"})
    assert "time" in str(ei.value).lower()


def test_invalid_day_raises() -> None:
    with pytest.raises(SchedulerError) as ei:
        Schedule.from_dict({"name": "x", "days": ["funday"], "text": "t"})
    assert "days" in str(ei.value).lower()


def test_missing_when_raises() -> None:
    with pytest.raises(SchedulerError) as ei:
        Schedule.from_dict({"name": "x", "text": "t"})
    assert "cron" in str(ei.value).lower() or "at" in str(ei.value).lower()


def test_missing_text_and_template_raises() -> None:
    with pytest.raises(SchedulerError) as ei:
        Schedule.from_dict({"name": "x", "cron": "0 9 * * *"})
    assert "template" in str(ei.value).lower() or "text" in str(ei.value).lower()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_add_and_list(sched: PostScheduler) -> None:
    s = Schedule(name="foo", cron="0 9 * * *", text="hello")
    sched.add(s)
    assert sched.get("foo").cron == "0 9 * * *"
    assert [x.name for x in sched.list_schedules()] == ["foo"]


def test_add_duplicate_raises(sched: PostScheduler) -> None:
    sched.add(Schedule(name="dup", cron="0 9 * * *", text="x"))
    with pytest.raises(SchedulerError):
        sched.add(Schedule(name="dup", cron="0 9 * * *", text="y"))


def test_remove(sched: PostScheduler) -> None:
    sched.add(Schedule(name="rm", cron="0 9 * * *", text="x"))
    assert sched.remove("rm") is True
    assert sched.remove("rm") is False
    with pytest.raises(SchedulerError):
        sched.get("rm")


def test_enable_disable(sched: PostScheduler) -> None:
    sched.add(Schedule(name="e", cron="0 9 * * *", text="x", enabled=True))
    sched.disable("e")
    assert sched.get("e").enabled is False
    sched.enable("e")
    assert sched.get("e").enabled is True


# ---------------------------------------------------------------------------
# next_run
# ---------------------------------------------------------------------------


def test_next_run_cron(sched: PostScheduler) -> None:
    s = Schedule(name="hourly", cron="0 * * * *", text="t")
    nxt = sched.next_run(s, now=FIXED_NOW)
    assert nxt is not None
    assert nxt > FIXED_NOW
    assert nxt.minute == 0


def test_next_run_weekly(sched: PostScheduler) -> None:
    # Wed Jun 17 2026 is weekday 2; next mon = 4 days later
    s = Schedule(name="mon", days=["mon"], time="09:00", text="t")
    nxt = sched.next_run(s, now=FIXED_NOW)
    assert nxt is not None
    assert nxt.weekday() == 0  # Monday
    assert nxt.hour == 9
    assert nxt.minute == 0


def test_next_run_one_shot_past(sched: PostScheduler) -> None:
    s = Schedule(
        name="once",
        at="2020-01-01T00:00:00Z",
        text="t",
    )
    assert sched.next_run(s, now=FIXED_NOW) is None


def test_next_run_one_shot_future(sched: PostScheduler) -> None:
    s = Schedule(
        name="once",
        at="2030-01-01T00:00:00Z",
        text="t",
    )
    nxt = sched.next_run(s, now=FIXED_NOW)
    assert nxt is not None
    assert nxt.year == 2030


def test_next_run_disabled(sched: PostScheduler) -> None:
    s = Schedule(name="x", cron="0 9 * * *", text="t", enabled=False)
    assert sched.next_run(s, now=FIXED_NOW) is None


# ---------------------------------------------------------------------------
# enqueue_due
# ---------------------------------------------------------------------------


def test_enqueue_due_creates_rows(sched: PostScheduler, tmp_path: Path) -> None:
    from linkedin_mcp.db import DB
    db = DB(tmp_path / "x.db")
    past = (FIXED_NOW - timedelta(minutes=2)).isoformat(timespec="seconds")
    sched.add(Schedule(name="past", at=past, text="hello", enabled=True))
    ids = sched.enqueue_due(db, now=FIXED_NOW)
    assert len(ids) == 1
    db.close()


def test_enqueue_due_skips_disabled(sched: PostScheduler, tmp_path: Path) -> None:
    from linkedin_mcp.db import DB
    db = DB(tmp_path / "x.db")
    past = (FIXED_NOW - timedelta(minutes=2)).isoformat(timespec="seconds")
    sched.add(Schedule(name="off", at=past, text="x", enabled=False))
    ids = sched.enqueue_due(db, now=FIXED_NOW)
    assert ids == []
    db.close()


def test_enqueue_due_skips_future(sched: PostScheduler, tmp_path: Path) -> None:
    from linkedin_mcp.db import DB
    db = DB(tmp_path / "x.db")
    future = (FIXED_NOW + timedelta(days=1)).isoformat(timespec="seconds")
    sched.add(Schedule(name="later", at=future, text="x"))
    ids = sched.enqueue_due(db, now=FIXED_NOW)
    assert ids == []
    db.close()