"""MCP tools for the carousel generator (v1.0.0)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..carousel import CarouselGenerator


def generate_carousel(text: str, title: str = "", max_chars_per_slide: int = 280) -> dict[str, Any]:
    """Turn a long post into a multi-slide carousel (PDF + PNGs).

    Returns dict with: title, slide_count, slides (text), pdf_path, png_paths.
    Falls back to text-only outline if Pillow isn't installed.
    """
    g = CarouselGenerator(max_chars_per_slide=max_chars_per_slide)
    c = g.render(text, title=title)
    return {
        "title": c.title,
        "slide_count": len(c.slides),
        "slides": [{"title": s.title, "body": s.body, "num": s.slide_num} for s in c.slides],
        "pdf_path": str(c.pdf_path) if c.pdf_path else None,
        "png_paths": [str(p) for p in c.png_paths],
    }