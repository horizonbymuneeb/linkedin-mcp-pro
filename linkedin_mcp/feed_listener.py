"""Feed listener + digest builder.

This is READ-ONLY — it never posts, likes, comments, or connects. Its job
is to keep an eye on the user's feed (and optionally their notifications)
and produce a daily digest of:

    - top posts from connections in the last 24h
    - trending topics among connections
    - posts mentioning you (mentions)
    - posts that hit your whitelist keywords (keyword alerts)
    - quiet warning signs (no engagement today, sudden spike, etc.)

The listener is rate-limited by the same SafetyGate, so even read-only
operations respect business hours and quotas.

It is safe to enable without dry_run — there is no action to enable.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass
class FeedItem:
    """A single item in the feed (post, mention, or notification)."""

    item_id: str
    author: str
    text: str
    url: str = ""
    posted_at: str = ""
    reactions: int = 0
    comments: int = 0
    reposts: int = 0
    item_type: str = "post"  # "post" | "mention" | "notification"
    matched_keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "author": self.author,
            "text": self.text,
            "url": self.url,
            "posted_at": self.posted_at,
            "reactions": self.reactions,
            "comments": self.comments,
            "reposts": self.reposts,
            "item_type": self.item_type,
            "matched_keywords": list(self.matched_keywords),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FeedItem:
        return cls(
            item_id=str(data.get("item_id", "")),
            author=str(data.get("author", "")),
            text=str(data.get("text", "")),
            url=str(data.get("url", "")),
            posted_at=str(data.get("posted_at", "")),
            reactions=int(data.get("reactions", 0)),
            comments=int(data.get("comments", 0)),
            reposts=int(data.get("reposts", 0)),
            item_type=str(data.get("item_type", "post")),
            matched_keywords=list(data.get("matched_keywords", [])),
        )


@dataclass
class DigestSection:
    """One section of the daily digest."""

    title: str
    items: list[FeedItem] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "items": [i.to_dict() for i in self.items],
        }


@dataclass
class DailyDigest:
    """The full daily digest."""

    generated_at: str
    window_start: str
    window_end: str
    sections: list[DigestSection] = field(default_factory=list)
    trending_keywords: list[tuple[str, int]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "sections": [s.to_dict() for s in self.sections],
            "trending_keywords": self.trending_keywords,
            "warnings": list(self.warnings),
        }

    def to_markdown(self) -> str:
        """Format digest as Markdown for Telegram/email."""
        lines: list[str] = []
        lines.append("# 📰 Daily LinkedIn Digest")
        lines.append(f"_Window: {self.window_start} → {self.window_end}_")
        lines.append(f"_Generated: {self.generated_at}_")
        lines.append("")

        if self.warnings:
            lines.append("## ⚠️ Warnings")
            for w in self.warnings:
                lines.append(f"- {w}")
            lines.append("")

        for section in self.sections:
            lines.append(f"## {section.title}")
            if section.summary:
                lines.append(f"_{section.summary}_")
                lines.append("")
            for item in section.items[:10]:
                emoji = {
                    "post": "📝",
                    "mention": "🔔",
                    "notification": "🔔",
                }.get(item.item_type, "📝")
                author = item.author or "(unknown)"
                snippet = (item.text[:200] + "…") if len(item.text) > 200 else item.text
                lines.append(f"### {emoji} {author}")
                lines.append(snippet)
                if item.url:
                    lines.append(f"[open]({item.url})")
                eng = (
                    f"👍{item.reactions} 💬{item.comments} 🔁{item.reposts}"
                )
                lines.append(f"_{eng}_")
                if item.matched_keywords:
                    lines.append(f"_matched: {', '.join(item.matched_keywords)}_")
                lines.append("")
            if len(section.items) > 10:
                lines.append(f"_…and {len(section.items) - 10} more_")
                lines.append("")

        if self.trending_keywords:
            lines.append("## 📈 Trending keywords (24h)")
            top = ", ".join(f"{k} ({c})" for k, c in self.trending_keywords[:10])
            lines.append(top)
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    def to_text(self) -> str:
        """Plain-text version for non-markdown channels."""
        return self.to_markdown().replace("**", "").replace("__", "").replace("#", "").strip()


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class FeedStore:
    """SQLite store for feed items + seen-IDs dedup.

    Schema:
        items(id PK, item_type, author, text, url, posted_at,
              reactions, comments, reposts, fetched_at, matched_keywords JSON)
        seen(item_id PK, first_seen)
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS items (
        item_id TEXT PRIMARY KEY,
        item_type TEXT NOT NULL DEFAULT 'post',
        author TEXT NOT NULL DEFAULT '',
        text TEXT NOT NULL DEFAULT '',
        url TEXT NOT NULL DEFAULT '',
        posted_at TEXT NOT NULL DEFAULT '',
        reactions INTEGER NOT NULL DEFAULT 0,
        comments INTEGER NOT NULL DEFAULT 0,
        reposts INTEGER NOT NULL DEFAULT 0,
        fetched_at TEXT NOT NULL,
        matched_keywords TEXT NOT NULL DEFAULT '[]'
    );
    CREATE INDEX IF NOT EXISTS idx_items_posted ON items(posted_at);
    CREATE INDEX IF NOT EXISTS idx_items_type ON items(item_type);

    CREATE TABLE IF NOT EXISTS seen (
        item_id TEXT PRIMARY KEY,
        first_seen TEXT NOT NULL
    );
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(self.SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def ingest(self, items: Iterable[FeedItem]) -> int:
        """Insert items, deduplicating by item_id. Returns new items added."""
        added = 0
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as conn:
            for item in items:
                # Dedup: try to insert; ignore if exists
                try:
                    conn.execute(
                        "INSERT INTO seen (item_id, first_seen) VALUES (?, ?)",
                        (item.item_id, now),
                    )
                except sqlite3.IntegrityError:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO items ("
                    "item_id, item_type, author, text, url, posted_at, "
                    "reactions, comments, reposts, fetched_at, matched_keywords"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        item.item_id,
                        item.item_type,
                        item.author,
                        item.text,
                        item.url,
                        item.posted_at,
                        item.reactions,
                        item.comments,
                        item.reposts,
                        now,
                        json.dumps(item.matched_keywords),
                    ),
                )
                added += 1
        return added

    def query(
        self,
        since: str | None = None,
        until: str | None = None,
        item_type: str | None = None,
        keyword: str | None = None,
        limit: int = 200,
    ) -> list[FeedItem]:
        """Query items with simple filters."""
        clauses: list[str] = []
        params: list[Any] = []
        if since:
            clauses.append("posted_at >= ?")
            params.append(since)
        if until:
            clauses.append("posted_at <= ?")
            params.append(until)
        if item_type:
            clauses.append("item_type = ?")
            params.append(item_type)
        if keyword:
            clauses.append("(LOWER(text) LIKE ? OR LOWER(matched_keywords) LIKE ?)")
            params.extend([f"%{keyword.lower()}%", f"%{keyword.lower()}%"])
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        # Safe: clauses come from hardcoded field names; user values are bound
        # via ? placeholders. S608 false positive.
        sql = (
            f"SELECT item_id, item_type, author, text, url, posted_at, "
            f"reactions, comments, reposts, matched_keywords "
            f"FROM items{where} ORDER BY posted_at DESC LIMIT ?"
        )  # noqa: S608
        params.append(limit)
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        results: list[FeedItem] = []
        for r in rows:
            try:
                kw = json.loads(r[9]) if r[9] else []
            except json.JSONDecodeError:
                kw = []
            results.append(
                FeedItem(
                    item_id=r[0],
                    item_type=r[1],
                    author=r[2],
                    text=r[3],
                    url=r[4],
                    posted_at=r[5],
                    reactions=r[6],
                    comments=r[7],
                    reposts=r[8],
                    matched_keywords=kw,
                )
            )
        return results

    def count(self, since: str | None = None) -> int:
        where = ""
        params: list[Any] = []
        if since:
            where = " WHERE posted_at >= ?"
            params.append(since)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM items{where}", params  # noqa: S608
            ).fetchone()
        return int(row[0]) if row else 0


# ---------------------------------------------------------------------------
# Listener (ingest helper)
# ---------------------------------------------------------------------------


class FeedListener:
    """In-memory filter that wraps a FeedStore and an optional live fetcher.

    In production, `fetch_fn` is a callable that returns a list of FeedItem
    scraped from LinkedIn. The listener runs the items through:

      1. Whitelist/blacklist keyword matcher
      2. Safety gate (read-only "feed_watch" action)
      3. Store ingestion (dedup by item_id)

    This separation lets us unit-test the entire pipeline without a real
    LinkedIn session — just pass a fake `fetch_fn`.
    """

    def __init__(
        self,
        store: FeedStore,
        *,
        safety_gate=None,
        keyword_whitelist: list[str] | None = None,
        keyword_blacklist: list[str] | None = None,
    ) -> None:
        self.store = store
        self.safety_gate = safety_gate
        self.keyword_whitelist = [w.lower() for w in (keyword_whitelist or [])]
        self.keyword_blacklist = [w.lower() for w in (keyword_blacklist or [])]

    def _annotate(self, items: list[FeedItem]) -> list[FeedItem]:
        """Mark items with matched keywords from whitelist (and filter blacklist)."""
        out: list[FeedItem] = []
        for item in items:
            text_lc = item.text.lower()
            # Filter blacklist
            if any(b in text_lc for b in self.keyword_blacklist):
                continue
            # Annotate whitelist matches
            if self.keyword_whitelist:
                item.matched_keywords = [
                    w for w in self.keyword_whitelist if w in text_lc
                ]
            out.append(item)
        return out

    def poll(
        self,
        fetch_fn,
        *,
        item_type: str = "post",
        max_items: int = 50,
    ) -> dict[str, Any]:
        """Run a poll cycle.

        Returns: {fetched, filtered, allowed, added, denied, reason}
        """
        # Safety check first (read-only "feed_watch" action)
        if self.safety_gate is not None:
            decision = self.safety_gate.check("feed_watch", target_text="")
            if not decision.allowed:
                return {
                    "fetched": 0,
                    "filtered": 0,
                    "allowed": 0,
                    "added": 0,
                    "denied": 1,
                    "reason": decision.reason,
                }

        # Fetch
        try:
            raw = fetch_fn(max_items=max_items, item_type=item_type) or []
        except Exception as exc:  # pragma: no cover - defensive
            return {
                "fetched": 0,
                "filtered": 0,
                "allowed": 0,
                "added": 0,
                "denied": 0,
                "reason": f"fetch error: {exc}",
            }

        # Filter + annotate
        filtered = self._annotate(list(raw))
        added = self.store.ingest(filtered)
        return {
            "fetched": len(raw),
            "filtered": len(filtered),
            "allowed": 1,
            "added": added,
            "denied": 0,
            "reason": "ok",
        }


# ---------------------------------------------------------------------------
# Digest builder
# ---------------------------------------------------------------------------


class DigestBuilder:
    """Build a DailyDigest from a FeedStore."""

    def __init__(self, store: FeedStore) -> None:
        self.store = store

    def build(self, *, lookback_hours: int = 24) -> DailyDigest:
        now = datetime.now(UTC)
        since = (now - timedelta(hours=lookback_hours)).isoformat()
        until = now.isoformat()

        sections: list[DigestSection] = []

        # Top posts (most reactions)
        all_items = self.store.query(since=since, until=until, limit=500)
        top_posts = sorted(all_items, key=lambda i: i.reactions, reverse=True)[:10]
        sections.append(
            DigestSection(
                title="🔥 Top posts from your network (24h)",
                items=top_posts,
                summary=f"{len(all_items)} items fetched in window",
            )
        )

        # Mentions
        mentions = [i for i in all_items if i.item_type == "mention"]
        if mentions:
            sections.append(
                DigestSection(
                    title="🔔 Mentions of you",
                    items=mentions,
                    summary=f"{len(mentions)} mentions",
                )
            )

        # Keyword alerts
        kw_items = [i for i in all_items if i.matched_keywords]
        if kw_items:
            sections.append(
                DigestSection(
                    title="🎯 Keyword alerts",
                    items=kw_items,
                    summary=f"{len(kw_items)} items match your tracked keywords",
                )
            )

        # Trending keywords
        counter: Counter[str] = Counter()
        for item in all_items:
            for kw in item.matched_keywords:
                counter[kw] += 1
        # Also pull frequent words (very simple)
        stop = {
            "the", "a", "an", "and", "or", "is", "are", "to", "of", "in", "on",
            "for", "with", "at", "by", "from", "as", "i", "we", "you", "it",
            "this", "that", "be", "have", "has", "had", "will", "would", "can",
            "could", "should", "may", "might", "do", "does", "did", "but", "if",
            "because", "so", "than", "then", "now", "more", "most", "some",
            "any", "all", "no", "not", "only", "just", "very", "too", "also",
        }
        for item in all_items:
            for word in item.text.lower().split():
                w = word.strip(".,!?;:()[]{}\"'\u201c\u201d")
                if len(w) >= 5 and w.isalpha() and w not in stop:
                    counter[w] += 1
        trending = counter.most_common(20)

        # Warnings
        warnings: list[str] = []
        if len(all_items) == 0:
            warnings.append(
                "No feed activity in the last 24h. Your network may be quiet, "
                "or the listener may need a poll cycle."
            )
        elif len(all_items) < 5:
            warnings.append(
                f"Only {len(all_items)} items in 24h — light activity. "
                "Consider expanding your network."
            )
        for item in all_items:
            if "hiring" in item.text.lower() or "job opening" in item.text.lower():
                warnings.append(
                    f"Hiring signal detected: '{item.author}' — review for relevance."
                )
                break  # only one warning of this kind

        return DailyDigest(
            generated_at=now.isoformat(),
            window_start=since,
            window_end=until,
            sections=sections,
            trending_keywords=trending,
            warnings=warnings,
        )
