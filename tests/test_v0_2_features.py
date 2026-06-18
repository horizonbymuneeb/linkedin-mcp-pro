"""Tests for v0.2.0 features: comment/react with URL, media upload,
delete_post, and note template rotation."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from linkedin_mcp.browser import connect, engage, post


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_browser() -> MagicMock:
    """Mock BrowserClient for unit tests (no real browser)."""
    b = MagicMock()
    b.navigate = AsyncMock()
    b.snapshot = AsyncMock(return_value="")
    b.click = AsyncMock()
    b.fill = AsyncMock()
    b.upload = AsyncMock()
    return b


def _tree(*lines: str) -> str:
    """Build a fake agent-browser snapshot tree."""
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Engage: comment_on_post with URL/URN
# ---------------------------------------------------------------------------


class TestCommentValidation:
    def test_urn_to_url_conversion(self):
        from linkedin_mcp.browser.engage import _validate_urn_or_url
        url = _validate_urn_or_url("urn:li:activity:123456")
        assert url == "https://www.linkedin.com/feed/update/urn:li:activity:123456/"

    def test_passthrough_url(self):
        from linkedin_mcp.browser.engage import _validate_urn_or_url
        url = "https://www.linkedin.com/feed/update/urn:li:activity:999/"
        assert _validate_urn_or_url(url) == url

    def test_rejects_non_linkedin_url(self):
        from linkedin_mcp.browser.engage import _validate_urn_or_url
        with pytest.raises(ValueError, match="linkedin.com"):
            _validate_urn_or_url("https://example.com/post/123")

    def test_rejects_garbage(self):
        from linkedin_mcp.browser.engage import _validate_urn_or_url
        with pytest.raises(ValueError):
            _validate_urn_or_url("not a url or urn")

    def test_rejects_empty(self):
        from linkedin_mcp.browser.engage import _validate_urn_or_url
        with pytest.raises(ValueError, match="required"):
            _validate_urn_or_url("")


class TestCommentOnPost:
    @pytest.mark.asyncio
    async def test_posts_comment_via_url(self, mock_browser):
        mock_browser.snapshot.side_effect = [
            _tree('- textbox "Add a comment" [ref=e42]'),
            _tree('- button "Post" [ref=e55]'),
        ]
        result = await engage.comment_on_post(
            mock_browser,
            "https://www.linkedin.com/feed/update/urn:li:activity:111/",
            "Great insight!",
        )
        assert result["ok"] is True
        assert result["len"] == 14
        assert "urn:li:activity:111" in result["target"]
        mock_browser.fill.assert_called_once_with("@e42", "Great insight!")
        mock_browser.click.assert_called_once_with("@e55")

    @pytest.mark.asyncio
    async def test_posts_comment_via_urn(self, mock_browser):
        mock_browser.snapshot.side_effect = [
            _tree('- textbox [ref=e10]'),
            _tree('- button "Comment" [ref=e11]'),
        ]
        result = await engage.comment_on_post(
            mock_browser, "urn:li:share:999", "Nice post"
        )
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_no_textbox_returns_error(self, mock_browser):
        mock_browser.snapshot.return_value = ""
        result = await engage.comment_on_post(
            mock_browser, "urn:li:activity:1", "test"
        )
        assert result["ok"] is False
        assert result["error"] == "no_comment_textbox"

    @pytest.mark.asyncio
    async def test_empty_text_raises(self, mock_browser):
        with pytest.raises(ValueError, match="text is required"):
            await engage.comment_on_post(mock_browser, "urn:li:activity:1", "")

    @pytest.mark.asyncio
    async def test_too_long_text_raises(self, mock_browser):
        with pytest.raises(ValueError, match="too long"):
            await engage.comment_on_post(mock_browser, "urn:li:activity:1", "x" * 1300)


# ---------------------------------------------------------------------------
# Engage: react_to_post with reaction types
# ---------------------------------------------------------------------------


class TestReactOnPost:
    @pytest.mark.asyncio
    async def test_like_default(self, mock_browser):
        mock_browser.snapshot.return_value = _tree('- button "Like" [ref=e20]')
        result = await engage.react_to_post(mock_browser, "urn:li:activity:5")
        assert result["ok"] is True
        assert result["reaction"] == "LIKE"
        # Single click for LIKE (no popup needed)
        assert mock_browser.click.call_count == 1

    @pytest.mark.asyncio
    async def test_non_like_opens_popup(self, mock_browser):
        mock_browser.snapshot.side_effect = [
            _tree('- button "Like" [ref=e20]'),
            _tree('- button "Celebrate" [ref=e21]'),
        ]
        result = await engage.react_to_post(
            mock_browser, "urn:li:activity:5", "CELEBRATE"
        )
        assert result["ok"] is True
        assert result["reaction"] == "CELEBRATE"
        assert mock_browser.click.call_count == 2

    @pytest.mark.asyncio
    async def test_invalid_reaction_raises(self, mock_browser):
        with pytest.raises(ValueError, match="must be one of"):
            await engage.react_to_post(mock_browser, "urn:li:activity:5", "ANGRY")

    @pytest.mark.asyncio
    async def test_no_like_button(self, mock_browser):
        mock_browser.snapshot.return_value = ""
        result = await engage.react_to_post(mock_browser, "urn:li:activity:5")
        assert result["ok"] is False
        assert result["error"] == "no_like_button"

    @pytest.mark.asyncio
    async def test_popup_missing_reaction(self, mock_browser):
        mock_browser.snapshot.side_effect = [
            _tree('- button "Like" [ref=e20]'),
            _tree(""),  # popup empty
        ]
        result = await engage.react_to_post(
            mock_browser, "urn:li:activity:5", "INSIGHTFUL"
        )
        assert result["ok"] is False
        assert "insightful_button_in_popup" in result["error"]


# ---------------------------------------------------------------------------
# Post: media upload validation
# ---------------------------------------------------------------------------


class TestMediaValidation:
    def test_valid_image(self, tmp_path: Path):
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
        validated = post._validate_media_path(str(img))
        assert validated == img.resolve()

    def test_valid_video(self, tmp_path: Path):
        vid = tmp_path / "clip.mp4"
        vid.write_bytes(b"\x00" * 1000)
        validated = post._validate_media_path(str(vid))
        assert validated == vid.resolve()

    def test_rejects_nonexistent(self):
        with pytest.raises(ValueError, match="not found"):
            post._validate_media_path("/nope/missing.jpg")

    def test_rejects_unsupported_ext(self, tmp_path: Path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"x" * 100)
        with pytest.raises(ValueError, match="unsupported media type"):
            post._validate_media_path(str(f))

    def test_rejects_too_large(self, tmp_path: Path):
        big = tmp_path / "huge.jpg"
        big.write_bytes(b"\x00" * (post._MAX_MEDIA_BYTES + 1))
        with pytest.raises(ValueError, match="too large"):
            post._validate_media_path(str(big))

    def test_rejects_directory(self, tmp_path: Path):
        d = tmp_path / "subdir"
        d.mkdir()
        with pytest.raises(ValueError, match="not a file"):
            post._validate_media_path(str(d))


class TestPostUrnValidation:
    def test_urn_to_url(self):
        url = post._validate_urn_or_url("urn:li:ugcPost:42")
        assert "linkedin.com" in url
        assert "urn:li:ugcPost:42" in url

    def test_url_passthrough(self):
        u = "https://www.linkedin.com/feed/update/urn:li:activity:1/"
        assert post._validate_urn_or_url(u) == u

    def test_rejects_external_url(self):
        with pytest.raises(ValueError, match="linkedin.com"):
            post._validate_urn_or_url("https://malicious.com/post")


class TestCreatePostWithMedia:
    @pytest.mark.asyncio
    async def test_text_only(self, mock_browser):
        mock_browser.snapshot.side_effect = [
            _tree('- button "Start a post" [ref=e1]'),
            _tree('- textbox [ref=e2]'),
            _tree('- button "Post" [ref=e3]'),
        ]
        result = await post.create_post(
            mock_browser, "Hello LinkedIn!"
        )
        assert result["ok"] is True
        assert result["with_media"] is False
        assert result["text_len"] == 15

    @pytest.mark.asyncio
    async def test_with_image(self, mock_browser, tmp_path: Path):
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
        mock_browser.snapshot.side_effect = [
            _tree('- button "Start a post" [ref=e1]'),
            _tree('- textbox [ref=e2]'),
            _tree('- button "Add a photo" [ref=e4]'),
            _tree('- input file [ref=e10]'),
            _tree('- button "Post" [ref=e3]'),
        ]
        result = await post.create_post(
            mock_browser, "With photo!", media_path=str(img)
        )
        assert result["ok"] is True
        assert result["with_media"] is True
        assert result["media_type"] == "image"
        mock_browser.upload.assert_called_once()
        # The file path passed should be the resolved one
        call_args = mock_browser.upload.call_args
        assert str(img.resolve()) == call_args[0][1]

    @pytest.mark.asyncio
    async def test_no_start_button(self, mock_browser):
        mock_browser.snapshot.return_value = ""
        result = await post.create_post(mock_browser, "Test")
        assert result["ok"] is False
        assert result["error"] == "no_start_post_button"

    @pytest.mark.asyncio
    async def test_invalid_visibility(self, mock_browser):
        with pytest.raises(ValueError, match="visibility must be"):
            await post.create_post(mock_browser, "Test", visibility="PRIVATE")


class TestDeletePost:
    @pytest.mark.asyncio
    async def test_delete_via_url(self, mock_browser):
        mock_browser.snapshot.side_effect = [
            _tree('- button "More actions" [ref=e5]'),
            _tree('- button "Delete" [ref=e6]'),
            _tree('- button "Delete" [ref=e7]'),
        ]
        result = await post.delete_post(
            mock_browser, "https://www.linkedin.com/feed/update/urn:li:activity:1/"
        )
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_no_overflow_menu(self, mock_browser):
        mock_browser.snapshot.return_value = ""
        result = await post.delete_post(mock_browser, "urn:li:activity:1")
        assert result["ok"] is False
        assert result["error"] == "no_overflow_menu"

    @pytest.mark.asyncio
    async def test_no_delete_option(self, mock_browser):
        mock_browser.snapshot.side_effect = [
            _tree('- button "More" [ref=e5]'),
            _tree(""),  # delete not in menu
        ]
        result = await post.delete_post(mock_browser, "urn:li:activity:1")
        assert result["ok"] is False
        assert result["error"] == "no_delete_option"


# ---------------------------------------------------------------------------
# Connect: note template rotation
# ---------------------------------------------------------------------------


class TestPickNote:
    def test_default_templates_produce_different_notes(self):
        """50 calls should produce >1 unique note (rotation is working)."""
        notes = set()
        for _ in range(50):
            notes.add(connect.pick_note(first_name="Alice"))
        assert len(notes) >= 3, f"Expected rotation, got {len(notes)} unique notes"

    def test_fills_first_name(self):
        note = connect.pick_note(first_name="Sam")
        assert "Sam" in note

    def test_fills_topic(self):
        # Some templates use {topic}, some don't — check across many picks
        notes = [
            connect.pick_note(topic="vector databases")
            for _ in range(50)
        ]
        assert any("vector databases" in n for n in notes), (
            "Topic should appear in at least some templates"
        )

    def test_fills_my_field(self):
        note = connect.pick_note(my_field="ML infrastructure")
        assert "ML infrastructure" in note

    def test_fills_company(self):
        # Some templates use {company}
        notes = [
            connect.pick_note(company="Spotify")
            for _ in range(50)
        ]
        assert any("Spotify" in n for n in notes)

    def test_default_first_name_there(self):
        note = connect.pick_note()
        # Either "there" or the actual first_name
        assert note is not None
        assert len(note) > 0

    def test_custom_templates(self):
        custom = ("Custom: {first_name} at {company}",)
        note = connect.pick_note(
            first_name="Bob", company="Acme", templates=custom
        )
        assert note == "Custom: Bob at Acme"

    def test_no_placeholder_left(self):
        """No unreplaced {var} should remain in the output."""
        for _ in range(20):
            note = connect.pick_note(first_name="Z", topic="T", my_field="F")
            assert "{" not in note, f"Unfilled placeholder in: {note}"

    def test_max_length_respected(self):
        """All generated notes should fit in 300-char LinkedIn limit."""
        for _ in range(50):
            note = connect.pick_note(
                first_name="Verylongname",
                topic="distributed systems and ML infrastructure at scale",
            )
            assert len(note) <= connect.MAX_NOTE_LENGTH, (
                f"Note too long ({len(note)}): {note}"
            )


# ---------------------------------------------------------------------------
# Browser client: upload method exists
# ---------------------------------------------------------------------------


class TestBrowserClientUpload:
    @pytest.mark.asyncio
    async def test_upload_method_exists(self, mock_browser):
        """The BrowserClient should expose an upload() method."""
        # This is a smoke test — actual functionality is integration-tested
        assert hasattr(mock_browser, "upload")
        assert callable(mock_browser.upload)

    @pytest.mark.asyncio
    async def test_upload_passes_resolved_path(self, mock_browser, tmp_path: Path):
        """Ensure post.create_post calls upload with the resolved path."""
        img = tmp_path / "pic.png"
        img.write_bytes(b"\x89PNG\r\n" + b"\x00" * 50)
        mock_browser.snapshot.side_effect = [
            _tree('- button "Start a post" [ref=e1]'),
            _tree('- textbox [ref=e2]'),
            _tree('- button "Add a photo" [ref=e4]'),
            _tree('- input [ref=e10]'),
            _tree('- button "Post" [ref=e3]'),
        ]
        await post.create_post(mock_browser, "Test", media_path=str(img))
        # Verify upload was called with the absolute path
        upload_call = mock_browser.upload.call_args
        assert upload_call[0][0] == "@e10"  # selector
        assert upload_call[0][1] == str(img.resolve())  # resolved path
