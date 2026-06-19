"""Regression tests for sidebar link rewriting in _render_static.

Without the rewrite, the shell contains href="/static/drafts.html" etc., but
the routes are registered at /drafts. Clicking such a link returns 404 and
the browser shows a blank un-styled page instead of the styled shell.

These tests pin the rewrite behavior so future refactors don't regress.
"""
from __future__ import annotations

import re
from pathlib import Path

from linkedin_mcp.web import _render_static, _STATIC_PAGES


PAGES_WITH_SHELL = [p for p in _STATIC_PAGES if (Path(__file__).resolve().parents[1] / "linkedin_mcp" / "static" / f"{p}.html").exists()]


def test_rewrites_static_links_to_clean_routes():
    """A page with a stale /static/X.html link should be rewritten to /X."""
    static_dir = Path(__file__).resolve().parents[1] / "linkedin_mcp" / "static"
    # Use a known-existing page; render via the helper.
    target = static_dir / "drafts.html"
    if not target.exists():
        return  # skipped if drafts.html missing in test env

    rendered = _render_static(target)

    # None of the sidebar/dev links should survive.
    assert 'href="/static/drafts.html"' not in rendered, (
        "Sidebar link /static/drafts.html should be rewritten to /drafts"
    )

    # The clean route should appear in the rendered output (sidebar nav).
    assert 'href="/drafts"' in rendered, (
        "Sidebar should contain clean /drafts link after rewrite"
    )


def test_index_link_rewritten_to_root():
    """The home page nav link /static/index.html → /."""
    static_dir = Path(__file__).resolve().parents[1] / "linkedin_mcp" / "static"
    target = static_dir / "index.html"
    if not target.exists():
        return

    rendered = _render_static(target)
    assert 'href="/static/index.html"' not in rendered
    # Root link should be present
    assert re.search(r'href="/(index\.html)?"', rendered) or 'href="/"' in rendered


def test_all_pages_render_with_rewritten_links():
    """Smoke: every page that uses the shell renders without /static/*.html links."""
    static_dir = Path(__file__).resolve().parents[1] / "linkedin_mcp" / "static"
    bad_links = []
    for page in PAGES_WITH_SHELL:
        f = static_dir / f"{page}.html"
        rendered = _render_static(f)
        # Look for any surviving /static/<page>.html link
        leftover = re.search(rf'href="/static/{page}\.html"', rendered)
        if leftover:
            bad_links.append(page)
    assert not bad_links, f"Pages with stale /static links: {bad_links}"


def test_shell_include_resolves():
    """The {% include "_shell.html" %} placeholder is replaced, not left literal."""
    static_dir = Path(__file__).resolve().parents[1] / "linkedin_mcp" / "static"
    target = static_dir / "drafts.html"
    if not target.exists():
        return
    rendered = _render_static(target)
    assert "{% include" not in rendered, "Include placeholder leaked through"
    # Shell always injects the tailwind config
    assert "tailwind.config" in rendered, "Shell Tailwind config missing"