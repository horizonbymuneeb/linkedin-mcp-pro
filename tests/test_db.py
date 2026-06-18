"""Unit tests for the SQLite-backed state layer.

Covers quota tracking, queue lifecycle, audit log, and session state.
Uses a tmp_path fixture for isolation — no real DB created.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from linkedin_mcp.db import DB, SCHEMA_VERSION, QuotaStatus


@pytest.fixture
def db(tmp_path: Path) -> DB:
    return DB(tmp_path / "test.db")


# === Schema ==================================================================


def test_schema_version_recorded(db: DB) -> None:
    v = db.get_state("schema_version")
    assert v == str(SCHEMA_VERSION)


def test_db_file_created_on_init(tmp_path: Path) -> None:
    p = tmp_path / "fresh.db"
    assert not p.exists()
    DB(p)
    assert p.exists()


# === Quotas ==================================================================


def test_get_quota_zero_initially(db: DB) -> None:
    q = db.get_quota("connection", limit=20)
    assert q.used == 0
    assert q.limit == 20
    assert q.remaining == 20
    assert q.zone == "green"


def test_increment_quota(db: DB) -> None:
    db.increment_quota("connection")
    db.increment_quota("connection")
    db.increment_quota("connection")
    q = db.get_quota("connection", limit=20)
    assert q.used == 3
    assert q.remaining == 17
    assert q.percent == pytest.approx(15.0)


def test_quota_zones(db: DB) -> None:
    # 50% = green
    for _ in range(10):
        db.increment_quota("connection")
    q = db.get_quota("connection", limit=20)
    assert q.zone == "green"

    # 70% = yellow
    for _ in range(4):
        db.increment_quota("connection")
    q = db.get_quota("connection", limit=20)
    assert q.zone == "yellow"

    # 95% = red
    for _ in range(5):
        db.increment_quota("connection")
    q = db.get_quota("connection", limit=20)
    assert q.zone == "red"

    # 100% = exhausted
    db.increment_quota("connection")
    q = db.get_quota("connection", limit=20)
    assert q.zone == "exhausted"
    assert q.remaining == 0


def test_increment_by_n(db: DB) -> None:
    db.increment_quota("post", n=5)
    q = db.get_quota("post", limit=10)
    assert q.used == 5


def test_reset_quota(db: DB) -> None:
    db.increment_quota("connection", n=10)
    db.reset_quota("connection")
    q = db.get_quota("connection", limit=20)
    assert q.used == 0


def test_get_all_quotas(db: DB) -> None:
    db.increment_quota("connection", n=5)
    db.increment_quota("post", n=1)
    db.increment_quota("message", n=3)
    limits = {"connection": 20, "post": 2, "message": 30, "comment": 30, "reaction": 100}
    qs = db.get_all_quotas(limits)
    assert len(qs) == 5
    used = {q.action: q.used for q in qs}
    assert used == {"connection": 5, "post": 1, "message": 3, "comment": 0, "reaction": 0}


def test_quota_status_property_arithmetic() -> None:
    q = QuotaStatus(action="x", day="2026-01-01", used=8, limit=10)
    assert q.remaining == 2
    assert q.percent == 80.0
    assert q.zone == "yellow"


# === Queue ===================================================================


def test_enqueue_returns_id(db: DB) -> None:
    qid = db.enqueue("connection", {"public_id": "alice"})
    assert isinstance(qid, int)
    assert qid > 0


def test_next_queued_marks_executing(db: DB) -> None:
    db.enqueue("post", {"text": "hello"})
    nxt = db.next_queued()
    assert nxt is not None
    assert nxt["action"] == "post"
    assert nxt["payload"] == {"text": "hello"}
    # Second call should return None — the only one is now executing
    assert db.next_queued() is None


def test_complete_queued(db: DB) -> None:
    qid = db.enqueue("post", {"text": "x"})
    nxt = db.next_queued()
    assert nxt is not None
    db.complete_queued(qid, "done", result={"post_urn": "urn:li:activity:1"})
    assert db.queue_size("pending") == 0


def test_queue_size_by_status(db: DB) -> None:
    db.enqueue("post", {"x": 1})
    db.enqueue("post", {"x": 2})
    db.enqueue("post", {"x": 3})
    assert db.queue_size("pending") == 3
    nxt = db.next_queued()
    assert nxt is not None
    assert db.queue_size("pending") == 2
    assert db.queue_size("executing") == 1


# === Audit log ===============================================================


def test_audit_returns_id(db: DB) -> None:
    aid = db.audit("connection", "success", target="linkedin.com/in/alice", detail={"note": "hi"})
    assert aid > 0


def test_audit_default_dry_run_is_false(db: DB) -> None:
    db.audit("post", "success")
    rows = db.get_audit(limit=1)
    assert rows[0]["dry_run"] == 0


def test_audit_with_dry_run(db: DB) -> None:
    db.audit("post", "dry_run", dry_run=True, target="me")
    rows = db.get_audit(limit=1)
    assert rows[0]["dry_run"] == 1


def test_audit_filter_by_action(db: DB) -> None:
    db.audit("post", "success")
    db.audit("connection", "success")
    db.audit("post", "failed")
    rows = db.get_audit(action="post", limit=10)
    assert len(rows) == 2
    assert all(r["action"] == "post" for r in rows)


def test_audit_limit(db: DB) -> None:
    for i in range(50):
        db.audit("post", "success")
    rows = db.get_audit(limit=10)
    assert len(rows) == 10


def test_audit_in_order_newest_first(db: DB) -> None:
    db.audit("post", "success", detail={"n": 1})
    db.audit("post", "success", detail={"n": 2})
    db.audit("post", "success", detail={"n": 3})
    rows = db.get_audit(limit=10)
    assert rows[0]["detail"] == json.dumps({"n": 3})
    assert rows[-1]["detail"] == json.dumps({"n": 1})


def test_audit_cleanup(db: DB) -> None:
    # Insert with old created_at (5 years ago)
    for _ in range(5):
        aid = db.audit("post", "success")
    # Manually backdate
    from datetime import datetime, timedelta, timezone
    five_years_ago = (
        datetime.now(timezone.utc) - timedelta(days=365 * 5)
    ).isoformat(timespec="seconds")
    with db.transaction() as conn:
        conn.execute("UPDATE audit_log SET created_at = ?", (five_years_ago,))
    deleted = db.cleanup_audit(retention_days=90)
    assert deleted == 5


# === Session state ===========================================================


def test_state_get_returns_none_when_missing(db: DB) -> None:
    assert db.get_state("nonexistent") is None


def test_state_set_and_get(db: DB) -> None:
    db.set_state("account_age_weeks", "3")
    assert db.get_state("account_age_weeks") == "3"


def test_state_set_overwrites(db: DB) -> None:
    db.set_state("foo", "bar")
    db.set_state("foo", "baz")
    assert db.get_state("foo") == "baz"
