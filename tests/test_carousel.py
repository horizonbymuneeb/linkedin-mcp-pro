"""Tests for carousel generator (v1.0.0)."""

from __future__ import annotations

from pathlib import Path

import pytest

from linkedin_mcp.carousel import Carousel, CarouselError, CarouselGenerator, Slide


@pytest.fixture
def gen(tmp_path: Path) -> CarouselGenerator:
    return CarouselGenerator(output_dir=tmp_path / "carousels")


def test_split_short_text_returns_one_slide(gen: CarouselGenerator) -> None:
    slides = gen.split_into_slides("Just one line of text here.")
    assert len(slides) == 1
    assert slides[0].title == "Just one line of text here."


def test_split_two_sentences(gen: CarouselGenerator) -> None:
    slides = gen.split_into_slides("First sentence. Second sentence. Third sentence here.")
    assert len(slides) >= 1
    assert "First" in slides[0].body or "First" in slides[0].title


def test_split_long_text_produces_multiple_slides(gen: CarouselGenerator) -> None:
    text = (
        "Title line here\n"
        "This is sentence one of the body. "
        "This is sentence two. "
        "This is sentence three. "
        "This is sentence four. "
        "This is sentence five. "
        "This is sentence six. "
        "This is sentence seven. "
        "This is sentence eight."
    )
    slides = gen.split_into_slides(text)
    assert len(slides) >= 2
    # Each slide should respect max_chars roughly
    for s in slides:
        assert len(s.body) <= gen.max_chars + 50  # small slack


def test_split_strips_markdown_header(gen: CarouselGenerator) -> None:
    slides = gen.split_into_slides("# My Header\n\nBody sentence here.")
    assert "#" not in slides[0].title
    assert "My Header" in slides[0].title


def test_split_empty_raises(gen: CarouselGenerator) -> None:
    with pytest.raises(CarouselError):
        gen.split_into_slides("")


def test_split_whitespace_only_raises(gen: CarouselGenerator) -> None:
    with pytest.raises(CarouselError):
        gen.split_into_slides("   \n\n  ")


def test_split_preserves_all_content(gen: CarouselGenerator) -> None:
    text = "Title\n\nFirst. Second. Third. Fourth. Fifth. Sixth. Seventh. Eighth. Ninth. Tenth."
    slides = gen.split_into_slides(text)
    combined = " ".join(s.title + " " + s.body for s in slides)
    for word in ["First", "Third", "Fifth", "Tenth"]:
        assert word in combined


def test_render_returns_carousel_object(gen: CarouselGenerator) -> None:
    text = "Title here\n\nA body sentence. Another body sentence."
    c = gen.render(text, title="Custom Title")
    assert isinstance(c, Carousel)
    assert c.title == "Custom Title"
    assert len(c.slides) >= 1


def test_render_without_pillow_falls_back(gen: CarouselGenerator) -> None:
    """If Pillow isn't installed, render() should still succeed with text-only output."""
    import sys
    saved = sys.modules.get("PIL")
    sys.modules["PIL"] = None  # force ImportError
    try:
        text = "Title here\n\nBody sentence one. Body sentence two."
        c = gen.render(text)
        assert len(c.slides) >= 1
        # No PNGs/PDF in fallback mode
        assert c.pdf_path is None
        assert c.png_paths == []
    finally:
        if saved is not None:
            sys.modules["PIL"] = saved
        else:
            sys.modules.pop("PIL", None)


def test_slide_num_increments_from_one(gen: CarouselGenerator) -> None:
    slides = gen.split_into_slides("Title\n\nA. B. C. D. E. F.")
    nums = [s.slide_num for s in slides]
    assert nums == list(range(1, len(slides) + 1))