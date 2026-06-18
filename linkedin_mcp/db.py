"""SQLite-backed state for linkedin-mcp-pro.

Three tables:
  - daily_quotas: rolling daily counter per action type
  - action_queue: pending actions waiting for jitter / business-hours release
  - audit_log: every action ever taken, with status, target, error
  - session_state: warmup state, last 429 time, etc.

Schema is small on purpose — quotas/queue/audit cover all safety needs.
All writes are wrapped in a context manager for atomicity.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

# Use UTC for all timestamps; convert at display time.
def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# Schema version. Bump on any structural change.
SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_quotas (
    day TEXT NOT NULL,                   -- 'YYYY-MM-DD' UTC
    action TEXT NOT NULL,                -- 'connection' | 'post' | 'message' | 'comment' | 'reaction'
    count INTEGER NOT NULL DEFAULT 0,
    last_action_at TEXT,
    PRIMARY KEY (day, action)
);

CREATE TABLE IF NOT EXISTS action_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    payload TEXT NOT NULL,               -- JSON
    scheduled_at TEXT NOT NULL,          -- ISO UTC, earliest time to run
    status TEXT NOT NULL DEFAULT 'pending',  -- pending|executing|done|failed|cancelled
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    error TEXT,
    result TEXT                          -- JSON
);
CREATE INDEX IF NOT EXISTS idx_queue_status_scheduled
    ON action_queue(status, scheduled_at);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    target TEXT,                         -- profile URL, post ID, etc.
    status TEXT NOT NULL,                -- 'success' | 'failed' | 'dry_run' | 'blocked_safety' | 'rate_limited'
    dry_run INTEGER NOT NULL DEFAULT 0,
    detail TEXT,                         -- JSON: extra context, error
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);

CREATE TABLE IF NOT EXISTS session_state (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT
);
"""


@dataclass
class QuotaStatus:
    action: str
    day: str
    used: int
    limit: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def percent(self) -> float:
        return (self.used / self.limit * 100) if self.limit else 0.0

    @property
    def zone(self) -> str:
        """green (<60%), yellow (60-89%), red (90-99%), exhausted (100%)."""
        if self.percent >= 100:
            return "exhausted"
        if self.percent >= 90:
            return "red"
        if self.percent >= 60:
            return "yellow"
        return "green"


class DB:
    """Thread-safe SQLite wrapper. One connection per DB instance, guarded by lock."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path), check_same_thread=False, isolation_level=None
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA)
            # Set schema version if missing
            self._conn.execute(
                "INSERT OR IGNORE INTO session_state(key, value, updated_at) "
                "VALUES (?, ?, ?)",
                ("schema_version", str(SCHEMA_VERSION), _now_utc()),
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    # -- Quota operations ---------------------------------------------------

    def get_quota(self, action: str, limit: int, day: Optional[str] = None) -> QuotaStatus:
        day = day or _today_utc()
        with self._lock:
            row = self._conn.execute(
                "SELECT count FROM daily_quotas WHERE day = ? AND action = ?",
                (day, action),
            ).fetchone()
        used = row["count"] if row else 0
        return QuotaStatus(action=action, day=day, used=used, limit=limit)

    def increment_quota(self, action: str, n: int = 1) -> QuotaStatus:
        """Atomically increment today's counter. Returns updated status.

        Note: this INCREMENTS regardless of cap — caller checks first via
        get_quota(). The cap is enforced at the safety layer.
        """
        day = _today_utc()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO daily_quotas(day, action, count, last_action_at) "
                "VALUES(?, ?, ?, ?) "
                "ON CONFLICT(day, action) DO UPDATE SET "
                "  count = count + excluded.count, "
                "  last_action_at = excluded.last_action_at",
                (day, action, n, _now_utc()),
            )
        # Read back with a default limit of 999 (caller's real cap may differ)
        return self.get_quota(action, limit=999)

    def reset_quota(self, action: str, day: Optional[str] = None) -> None:
        day = day or _today_utc()
        with self.transaction() as conn:
            conn.execute(
                "DELETE FROM daily_quotas WHERE day = ? AND action = ?",
                (day, action),
            )

    def get_all_quotas(self, limits: dict[str, int]) -> list[QuotaStatus]:
        return [self.get_quota(a, lim) for a, lim in limits.items()]

    # -- Queue operations ---------------------------------------------------

    def enqueue(
        self,
        action: str,
        payload: dict[str, Any],
        scheduled_at: Optional[str] = None,
    ) -> int:
        scheduled = scheduled_at or _now_utc()
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO action_queue(action, payload, scheduled_at, created_at) "
                "VALUES(?, ?, ?, ?)",
                (action, json.dumps(payload), scheduled, _now_utc()),
            )
            return cur.lastrowid

    def next_queued(self) -> Optional[dict[str, Any]]:
        """Get the next ready pending action, mark it as executing. Returns dict or None."""
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM action_queue "
                "WHERE status = 'pending' AND scheduled_at <= ? "
                "ORDER BY scheduled_at ASC LIMIT 1",
                (_now_utc(),),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE action_queue SET status='executing', started_at=? WHERE id=?",
                (_now_utc(), row["id"]),
            )
            return {
                "id": row["id"],
                "action": row["action"],
                "payload": json.loads(row["payload"]),
                "scheduled_at": row["scheduled_at"],
            }

    def complete_queued(
        self, queue_id: int, status: str, result: Optional[dict] = None, error: Optional[str] = None
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE action_queue SET status=?, completed_at=?, result=?, error=? "
                "WHERE id=?",
                (status, _now_utc(), json.dumps(result) if result else None, error, queue_id),
            )

    def queue_size(self, status: str = "pending") -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM action_queue WHERE status = ?", (status,)
            ).fetchone()
        return row["c"]

    # -- Audit log ----------------------------------------------------------

    def audit(
        self,
        action: str,
        status: str,
        target: Optional[str] = None,
        dry_run: bool = False,
        detail: Optional[dict[str, Any]] = None,
    ) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO audit_log(action, target, status, dry_run, detail, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                (action, target, status, int(dry_run), json.dumps(detail) if detail else None, _now_utc()),
            )
            return cur.lastrowid

    def get_audit(
        self, action: Optional[str] = None, limit: int = 50, since: Optional[str] = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM audit_log"
        params: list[Any] = []
        if action or since:
            clauses = []
            if action:
                clauses.append("action = ?")
                params.append(action)
            if since:
                clauses.append("created_at >= ?")
                params.append(since)
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def cleanup_audit(self, retention_days: int) -> int:
        """Delete audit rows older than retention. Returns count deleted."""
        from datetime import timedelta
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=retention_days)
        ).isoformat(timespec="seconds")
        with self.transaction() as conn:
            cur = conn.execute("DELETE FROM audit_log WHERE created_at < ?", (cutoff,))
            return cur.rowcount

    # -- Session state ------------------------------------------------------

    def get_state(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM session_state WHERE key=?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def set_state(self, key: str, value: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO session_state(key, value, updated_at) VALUES(?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, _now_utc()),
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()


if __name__ == "__main__":
    # Smoke test
    db = DB(Path("./data/linkedin-mcp-pro.db"))
    db.increment_quota("connection")
    db.increment_quota("connection")
    q = db.get_quota("connection", limit=20)
    print(f"Quota: {q.used}/{q.limit} ({q.percent:.0f}%, zone={q.zone})")
    qid = db.enqueue("post", {"text": "hello"}, _now_utc())
    print(f"Enqueued: id={qid}")
    nxt = db.next_queued()
    print(f"Next: {nxt['action']} payload={nxt['payload']}")
    db.complete_queued(nxt["id"], "done", result={"post_id": "abc"})
    aid = db.audit("post", "success", target="me", detail={"post_id": "abc"})
    print(f"Audit: id={aid}")
    print(f"Recent: {db.get_audit(limit=3)}")
    db.close()
