"""Tests for RSS auto-poster (v0.6.0)."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from linkedin_mcp.rss_poster import Feed, RSSError, RSSPoster


SAMPLE_RSS = b"""<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <link>https://example.com</link>
    <description>A test</description>
    <item>
      <title>First post</title>
      <link>https://example.com/1</link>
      <guid>https://example.com/1</guid>
      <description>The first one</description>
    </item>
    <item>
      <title>Second post</title>
      <link>https://example.com/2</link>
      <guid>https://example.com/2</guid>
      <description>The second</description>
    </item>
  </channel>
</rss>"""


SAMPLE_ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Feed</title>
  <entry>
    <id>tag:example.com,2026:1</id>
    <title>Atom Entry One</title>
    <link href="https://example.com/a1"/>
    <summary>Summary text</summary>
  </entry>
</feed>"""


@pytest.fixture
def poster(tmp_path: Path) -> RSSPoster:
    return RSSPoster(
        feeds_path=tmp_path / "feeds.yaml",
        seen_path=tmp_path / "seen.json",
    )


def test_empty_list(poster: RSSPoster) -> None:
    assert poster.list_feeds() == []


def test_add_and_list(poster: RSSPoster) -> None:
    poster.add(Feed(name="tech", url="https://example.com/rss"))
    feeds = poster.list_feeds()
    assert len(feeds) == 1
    assert feeds[0].name == "tech"


def test_add_duplicate_raises(poster: RSSPoster) -> None:
    poster.add(Feed(name="x", url="https://e.com"))
    with pytest.raises(RSSError):
        poster.add(Feed(name="x", url="https://e.com"))


def test_add_requires_url(poster: RSSPoster) -> None:
    with pytest.raises(RSSError):
        poster.add(Feed(name="x", url=""))


def test_remove(poster: RSSPoster) -> None:
    poster.add(Feed(name="x", url="https://e.com"))
    assert poster.remove("x") is True
    assert poster.remove("x") is False


def test_get_unknown_raises(poster: RSSPoster) -> None:
    with pytest.raises(RSSError):
        poster.get("ghost")


def test_parse_rss_basic(poster: RSSPoster) -> None:
    items = poster._parse_feed(SAMPLE_RSS)
    assert len(items) == 2
    assert items[0]["title"] == "First post"
    assert items[0]["guid"] == "https://example.com/1"


def test_parse_atom_basic(poster: RSSPoster) -> None:
    items = poster._parse_feed(SAMPLE_ATOM)
    assert len(items) == 1
    assert items[0]["title"] == "Atom Entry One"


def test_parse_invalid_xml_raises(poster: RSSPoster) -> None:
    with pytest.raises(RSSError):
        poster._parse_feed(b"<not xml")


def test_format_post_with_prefix(poster: RSSPoster) -> None:
    feed = Feed(name="x", url="https://e.com", text_prefix="New from ")
    text = poster._format_post(feed, {
        "title": "Article", "link": "https://example.com/a", "guid": "g",
        "description": "x",
    })
    assert "New from" in text
    assert "Article" in text
    assert "https://example.com/a" in text


def test_poll_with_mock_urlopen(poster: RSSPoster) -> None:
    poster.add(Feed(name="tech", url="https://example.com/rss"))
    with mock.patch.object(poster, "_fetch", return_value=SAMPLE_RSS):
        result = poster.poll()
    assert result["feeds_polled"] == 1
    assert len(result["new_posts"]) == 2
    assert result["new_posts"][0]["feed"] == "tech"
    assert result["new_posts"][0]["text"]  # non-empty


def test_poll_skips_already_seen(poster: RSSPoster) -> None:
    poster.add(Feed(name="tech", url="https://example.com/rss"))
    with mock.patch.object(poster, "_fetch", return_value=SAMPLE_RSS):
        result1 = poster.poll()
    assert len(result1["new_posts"]) == 2
    with mock.patch.object(poster, "_fetch", return_value=SAMPLE_RSS):
        result2 = poster.poll()
    assert len(result2["new_posts"]) == 0  # all seen


def test_poll_respects_limit(poster: RSSPoster) -> None:
    poster.add(Feed(name="tech", url="https://example.com/rss"))
    with mock.patch.object(poster, "_fetch", return_value=SAMPLE_RSS):
        result = poster.poll(limit_per_feed=1)
    assert len(result["new_posts"]) == 1


def test_poll_skips_disabled(poster: RSSPoster) -> None:
    poster.add(Feed(name="tech", url="https://example.com/rss", enabled=False))
    with mock.patch.object(poster, "_fetch", return_value=SAMPLE_RSS):
        result = poster.poll()
    assert result["feeds_polled"] == 0


def test_poll_handles_fetch_error(poster: RSSPoster) -> None:
    poster.add(Feed(name="broken", url="https://broken.example/rss"))
    with mock.patch.object(poster, "_fetch", side_effect=Exception("network down")):
        result = poster.poll()
    assert result["feeds_polled"] == 1
    assert len(result["new_posts"]) == 0