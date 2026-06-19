"""Tests for feed listener + digest builder."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from linkedin_mcp.ban_safety import (
    BusinessHours,
    CooldownRange,
    DailyLimits,
    HourlyLimits,
    SafetyConfig,
    SafetyGate,
    SafetyTracker,
)
from linkedin_mcp.feed_listener import (
    DailyDigest,
    DigestBuilder,
    DigestSection,
    FeedItem,
    FeedListener,
    FeedStore,
)

# ---------------------------------------------------------------------------
# FeedStore
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> FeedStore:
    return FeedStore(tmp_path / "feed.db")


def _make_item(
    item_id: str = "u1",
    text: str = "Hello world",
    author: str = "Alice",
    item_type: str = "post",
    reactions: int = 0,
    comments: int = 0,
    posted_at: str | None = None,
    matched_keywords: list[str] | None = None,
) -> FeedItem:
    return FeedItem(
        item_id=item_id,
        author=author,
        text=text,
        item_type=item_type,
        reactions=reactions,
        comments=comments,
        posted_at=posted_at or datetime.now(UTC).isoformat(),
        matched_keywords=matched_keywords or [],
    )


def test_store_ingest_dedup(store: FeedStore):
    added = store.ingest([_make_item("a"), _make_item("b"), _make_item("a")])
    assert added == 2
    assert store.count() == 2


def test_store_query_filters(store: FeedStore):
    now = datetime.now(UTC)
    store.ingest([
        _make_item("a", text="Python rocks", posted_at=now.isoformat()),
        _make_item("b", text="Other", posted_at=(now - timedelta(days=2)).isoformat()),
        _make_item("c", text="ML is great", item_type="mention",
                   posted_at=now.isoformat()),
    ])
    recent = store.query(since=(now - timedelta(hours=12)).isoformat())
    assert {i.item_id for i in recent} == {"a", "c"}
    mentions = store.query(item_type="mention")
    assert len(mentions) == 1
    assert mentions[0].item_id == "c"


def test_store_query_keyword(store: FeedStore):
    store.ingest([
        _make_item("a", text="Python is great", matched_keywords=["python"]),
        _make_item("b", text="Java is fine"),
    ])
    found = store.query(keyword="python")
    assert len(found) == 1
    assert found[0].item_id == "a"


# ---------------------------------------------------------------------------
# FeedListener
# ---------------------------------------------------------------------------


def test_listener_poll_with_safety_gate(tmp_path: Path):
    store = FeedStore(tmp_path / "f.db")
    tracker = SafetyTracker(tmp_path / "s.db")
    cfg = SafetyConfig(
        dry_run=False,  # feed_watch must actually run for the test
        business_hours=BusinessHours(start=0, end=24),
        cooldown_seconds=CooldownRange(min=0, max=0),
        daily_limits=DailyLimits(feed_watch=2, account_age_days_required=0),
        hourly_limits=HourlyLimits(),
    )
    gate = SafetyGate(cfg, tracker)
    listener = FeedListener(store, safety_gate=gate)

    def fake_fetch(max_items: int = 50, item_type: str = "post"):
        return [
            _make_item("a", text="AI news"),
            _make_item("b", text="Another post"),
        ]

    r1 = listener.poll(fake_fetch)
    r2 = listener.poll(fake_fetch)
    r3 = listener.poll(fake_fetch)
    assert r1["added"] == 2
    assert r2["added"] == 0  # dedup
    assert r3["denied"] == 1
    assert "feed_watch" in r3["reason"] or "limit" in r3["reason"]


def test_listener_keyword_filter(tmp_path: Path):
    store = FeedStore(tmp_path / "f.db")
    listener = FeedListener(
        store, keyword_whitelist=["python"], keyword_blacklist=["spam"]
    )
    fetched = [
        _make_item("a", text="Python is great"),
        _make_item("b", text="Spam Spam everywhere"),
        _make_item("c", text="I like turtles"),
    ]
    r = listener.poll(lambda **kw: fetched, max_items=10)
    assert r["fetched"] == 3
    assert r["filtered"] == 2  # blacklist dropped "b"
    assert r["added"] == 2
    items = store.query()
    matched = [i for i in items if i.matched_keywords]
    assert len(matched) == 1
    assert "python" in matched[0].matched_keywords


def test_listener_fetch_error(tmp_path: Path):
    store = FeedStore(tmp_path / "f.db")
    listener = FeedListener(store)

    def boom(**kw):
        raise RuntimeError("network down")

    r = listener.poll(boom)
    assert r["denied"] == 0
    assert "fetch error" in r["reason"]


# ---------------------------------------------------------------------------
# Digest
# ---------------------------------------------------------------------------


def test_digest_to_markdown_contains_sections(tmp_path: Path):
    store = FeedStore(tmp_path / "f.db")
    now = datetime.now(UTC)
    store.ingest([
        _make_item("a", text="Python and AI are the future", reactions=100,
                   matched_keywords=["python", "ai"],
                   posted_at=now.isoformat()),
        _make_item("b", text="Random post", reactions=10,
                   posted_at=now.isoformat()),
    ])
    builder = DigestBuilder(store)
    digest = builder.build()
    md = digest.to_markdown()
    assert "Daily LinkedIn Digest" in md
    assert "Top posts" in md
    assert "Python" in md or "python" in md
    assert "Trending keywords" in md


def test_digest_includes_mentions(tmp_path: Path):
    store = FeedStore(tmp_path / "f.db")
    store.ingest([
        _make_item("a", item_type="mention", text="@you great work!"),
    ])
    digest = DigestBuilder(store).build()
    titles = [s.title for s in digest.sections]
    assert any("Mentions" in t for t in titles)


def test_digest_warning_quiet_feed(tmp_path: Path):
    store = FeedStore(tmp_path / "f.db")
    digest = DigestBuilder(store).build()
    assert any("quiet" in w.lower() or "No feed" in w for w in digest.warnings)


def test_digest_warning_hiring(tmp_path: Path):
    store = FeedStore(tmp_path / "f.db")
    store.ingest([
        _make_item("a", text="We are hiring engineers, send your CV"),
    ])
    digest = DigestBuilder(store).build()
    assert any("Hiring" in w for w in digest.warnings)


def test_digest_to_dict_round_trip():
    d = DailyDigest(
        generated_at="2026-06-19T00:00:00Z",
        window_start="2026-06-18T00:00:00Z",
        window_end="2026-06-19T00:00:00Z",
        sections=[DigestSection(title="Top", items=[_make_item("a")])],
        trending_keywords=[("python", 5)],
        warnings=["warn"],
    )
    data = d.to_dict()
    assert data["generated_at"].startswith("2026")
    assert data["sections"][0]["items"][0]["item_id"] == "a"


def test_digest_to_text_strips_markdown():
    d = DailyDigest(
        generated_at="2026-06-19T00:00:00Z",
        window_start="2026-06-18T00:00:00Z",
        window_end="2026-06-19T00:00:00Z",
        sections=[DigestSection(title="Top", items=[_make_item("a", text="hello")])],
    )
    text = d.to_text()
    assert "#" not in text
    assert "hello" in text
