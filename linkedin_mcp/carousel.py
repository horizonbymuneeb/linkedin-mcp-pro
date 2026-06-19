"""Carousel generator for linkedin-mcp-pro (v1.0.0).

Turns a long post / outline into a sequence of square images ready
to upload as a LinkedIn carousel (PDF document, multi-image post).

Pipeline:
  1. Split long-form text into slides (heuristic: sentence boundary + max chars)
  2. Render each slide to PNG via PIL (Pillow)
  3. Combine all PNGs into a single multi-page PDF
  4. PDF is the upload format LinkedIn's "Add document" expects

Pillow is the only new dep (already a transitive of matplotlib,
fastapi image handling, etc.). Falls back to "outline only" mode
when Pillow isn't installed.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional


class CarouselError(Exception):
    """Raised for any carousel generation failure."""


@dataclass
class Slide:
    """A single slide's text (rendered separately)."""

    title: str
    body: str
    slide_num: int = 0


@dataclass
class Carousel:
    """A generated carousel ready to upload."""

    slides: list[Slide] = field(default_factory=list)
    cover: Optional[Slide] = None
    pdf_path: Optional[Path] = None
    png_paths: list[Path] = field(default_factory=list)
    title: str = ""


class CarouselGenerator:
    """Convert text to a multi-slide carousel (PNG + PDF).

    Heuristic: title = first non-empty line; slides = subsequent
    sentences/paragraphs split at sentence boundaries, capped at
    ``max_chars_per_slide`` (default 280 — fits LinkedIn's
    overlay-text size).
    """

    DEFAULT_MAX_CHARS = 280
    DEFAULT_SLIDE_SIZE = (1080, 1080)  # square, IG-style
    FONT_SIZE_TITLE = 64
    FONT_SIZE_BODY = 36

    def __init__(
        self,
        output_dir: str | Path | None = None,
        max_chars_per_slide: int = DEFAULT_MAX_CHARS,
    ):
        self.output_dir = Path(
            output_dir
            or os.environ.get("LINKEDIN_MCP_CAROUSEL_DIR")
            or (Path.home() / ".linkedin-mcp" / "carousels")
        )
        self.max_chars = max_chars_per_slide

    # -- Text → slides ---------------------------------------------------

    @staticmethod
    def _split_into_sentences(text: str) -> list[str]:
        """Naive sentence splitter (no NLP dep)."""
        # Split on '. ' or '! ' or '? ' but keep terminator
        parts = re.split(r"(?<=[.!?])\s+", text.strip())
        return [p.strip() for p in parts if p.strip()]

    @staticmethod
    def _first_line(text: str) -> str:
        for line in text.splitlines():
            line = line.strip()
            if line:
                return line
        return ""

    def split_into_slides(self, text: str) -> list[Slide]:
        """Split a long post into slides.

        First slide gets the first line as title + first 1-2 sentences as body.
        Subsequent slides are sentences grouped up to ``max_chars``.
        Returns a list with at least 1 slide.
        """
        text = text.strip()
        if not text:
            raise CarouselError("Cannot split empty text into slides")
        title = self._first_line(text).rstrip("#").strip()
        rest = text[len(title):].strip() if text.startswith(title) else text
        # Strip leading # if markdown header
        title = re.sub(r"^#+\s*", "", title)
        sentences = self._split_into_sentences(rest)
        slides: list[Slide] = []
        if not sentences:
            # Single-line input → title-only slide
            return [Slide(title=title, body="", slide_num=1)]
        # First slide: title + first 1-2 sentences
        first_chunk: list[str] = []
        while sentences and sum(len(s) + 1 for s in first_chunk) < self.max_chars and len(first_chunk) < 2:
            first_chunk.append(sentences.pop(0))
        slides.append(
            Slide(
                title=title,
                body=" ".join(first_chunk),
                slide_num=len(slides) + 1,
            )
        )
        # Subsequent slides: group remaining sentences
        current: list[str] = []
        while sentences:
            s = sentences[0]
            projected = sum(len(x) + 1 for x in current) + len(s)
            if current and projected > self.max_chars:
                slides.append(
                    Slide(
                        title=f"Slide {len(slides) + 1}",
                        body=" ".join(current),
                        slide_num=len(slides) + 1,
                    )
                )
                current = []
            else:
                current.append(sentences.pop(0))
        if current:
            slides.append(
                Slide(
                    title=f"Slide {len(slides) + 1}",
                    body=" ".join(current),
                    slide_num=len(slides) + 1,
                )
            )
        # Renumber cleanly
        for i, s in enumerate(slides, 1):
            s.slide_num = i
        return slides

    # -- Render ----------------------------------------------------------

    def render(
        self,
        text: str,
        *,
        title: str = "",
        include_cover: bool = True,
    ) -> Carousel:
        """Render the carousel to PNGs + a single multi-page PDF.

        Falls back to ``outline_only`` (no PDF/PNG) if Pillow isn't installed.
        """
        slides = self.split_into_slides(text)
        if title:
            slides[0].title = title
        c = Carousel(slides=slides, title=title or slides[0].title)
        if include_cover:
            c.cover = Slide(
                title=title or slides[0].title,
                body="",
                slide_num=0,
            )
        # Try to render — fall back gracefully if Pillow missing
        try:
            self._render_pngs(c)
            self._combine_pdf(c)
        except ImportError:
            # Pillow not installed — return text-only outline
            return c
        return c

    def _render_pngs(self, c: Carousel) -> None:
        from PIL import Image, ImageDraw, ImageFont
        try:
            font_title = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                self.FONT_SIZE_TITLE,
            )
        except OSError:
            font_title = ImageFont.load_default()
        try:
            font_body = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                self.FONT_SIZE_BODY,
            )
        except OSError:
            font_body = ImageFont.load_default()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        for slide in c.slides:
            img = Image.new("RGB", self.DEFAULT_SLIDE_SIZE, color="white")
            draw = ImageDraw.Draw(img)
            # Wrap title
            title_y = 80
            title_text = slide.title[:60]
            draw.text((60, title_y), title_text, fill="black", font=font_title)
            # Wrap body
            body_y = 220
            words = slide.body.split()
            lines: list[str] = []
            current = ""
            for word in words:
                test = (current + " " + word).strip()
                if len(test) > 35 and current:
                    lines.append(current)
                    current = word
                else:
                    current = test
            if current:
                lines.append(current)
            for i, line in enumerate(lines[:12]):  # max 12 lines visible
                draw.text((60, body_y + i * 56), line, fill="#333", font=font_body)
            # Footer: slide N of M
            footer = f"{slide.slide_num} / {len(c.slides)}"
            draw.text(
                (self.DEFAULT_SLIDE_SIZE[0] - 140, self.DEFAULT_SLIDE_SIZE[1] - 80),
                footer,
                fill="#999",
                font=font_body,
            )
            path = self.output_dir / f"slide_{slide.slide_num:02d}.png"
            img.save(path)
            c.png_paths.append(path)

    def _combine_pdf(self, c: Carousel) -> None:
        if not c.png_paths:
            return
        from PIL import Image
        first = Image.open(c.png_paths[0])
        rest = [Image.open(p) for p in c.png_paths[1:]]
        pdf_path = self.output_dir / f"{_safe(c.title or 'carousel')}.pdf"
        first.save(pdf_path, "PDF", resolution=72.0, save_all=True, append_images=rest)
        c.pdf_path = pdf_path


def _safe(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", name)[:60].strip("_") or "carousel"