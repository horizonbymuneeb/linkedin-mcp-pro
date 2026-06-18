"""MCP tools for post templates (v0.5.0).

Four tools, all metadata-only (no SafetyGuard — these never post to
LinkedIn on their own). Use ``render_template`` to produce text, then
call ``create_post`` to publish.

Tools:
    list_templates() -> list
        Returns one entry per template (dict with name/description/
        tags/default_vars/body_length/path).

    get_template(name: str) -> dict
        Returns the full template document.

    render_template(name: str, variables: dict, strict: bool = False) -> str
        Returns the rendered text. ``strict=True`` raises via the
        server's error path if a variable is missing.

    save_template(name, body, description="", tags=[], default_vars={}) -> dict
        Persists a template. Returns the saved template's metadata.

    delete_template(name: str) -> dict
        Removes a template file. Returns ``{"deleted": True}``.
"""

from __future__ import annotations

from typing import Any

from ..templates import Template, TemplateError, TemplatesStore


def _store() -> TemplatesStore:
    """One store per call — cheap, stateless, file-backed."""
    return TemplatesStore()


def _template_summary(t: Template) -> dict[str, Any]:
    return {
        "name": t.name,
        "description": t.description,
        "tags": list(t.tags),
        "default_vars": dict(t.default_vars),
        "body_length": len(t.body),
        "path": str(t.path) if t.path else None,
    }


def list_templates() -> list[dict[str, Any]]:
    """List all post templates in the store."""
    try:
        return [_template_summary(t) for t in _store().list_templates()]
    except TemplateError as e:
        raise ValueError(f"Failed to list templates: {e}") from e


def get_template(name: str) -> dict[str, Any]:
    """Return a single template's full document."""
    try:
        t = _store().get(name)
    except TemplateError as e:
        raise ValueError(str(e)) from e
    return t.to_dict() | {"path": str(t.path) if t.path else None}


def render_template(
    name: str,
    variables: dict[str, Any] | None = None,
    strict: bool = False,
) -> str:
    """Render a template's body with the given variables.

    Built-in variables (``{date}``, ``{time}``, ``{day_of_week}``,
    ``{week_number}``, ``{month}``, ``{year}``) are auto-filled.
    Template-level ``default_vars`` are applied first, then the
    caller's ``variables`` win on conflict.

    Returns the rendered string. With ``strict=False`` (default),
    missing placeholders are left verbatim; with ``strict=True``
    a ``ValueError`` is raised.
    """
    try:
        return _store().render(name, variables or {}, strict=strict)
    except TemplateError as e:
        raise ValueError(str(e)) from e


def save_template(
    name: str,
    body: str,
    description: str = "",
    tags: list[str] | None = None,
    default_vars: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create or overwrite a template."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")
    if not isinstance(body, str):
        raise ValueError("body must be a string")
    try:
        tpl = Template(
            name=name,
            body=body,
            description=description or "",
            tags=list(tags or []),
            default_vars=dict(default_vars or {}),
        )
        saved = _store().save(tpl)
    except TemplateError as e:
        raise ValueError(str(e)) from e
    return _template_summary(saved)


def delete_template(name: str) -> dict[str, Any]:
    """Delete a template by name. Raises ValueError if it doesn't exist."""
    try:
        _store().delete(name)
    except TemplateError as e:
        raise ValueError(str(e)) from e
    return {"deleted": True, "name": name}
