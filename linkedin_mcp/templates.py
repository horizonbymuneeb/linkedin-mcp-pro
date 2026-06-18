"""Post Templates for linkedin-mcp-pro (v0.5.0).

Reusable LinkedIn post templates with ``{variable}`` placeholders.

Templates are stored as YAML files in ``~/.linkedin-mcp/templates/`` by
default. Override with the ``LINKEDIN_MCP_TEMPLATES_DIR`` environment
variable — useful for tests, Docker volume mounts, or team-shared dirs.

YAML schema (one file per template)::

    name: weekly-update
    description: My standard Monday wrap-up post
    body: |
      Happy {day_of_week}! Here's what I shipped this week:

      {body}

      — posted via linkedin-mcp-pro on {date}
    tags:
      - weekly
      - update
      - monday
    default_vars:
      body: "Shipped a new MCP tool for LinkedIn."

Built-in variables are auto-filled before user variables resolve:

    {date}        2026-06-18
    {time}        14:32:08
    {day_of_week} Wednesday
    {week_number} 25
    {month}       06
    {year}        2026

User-supplied variables override built-ins if the names collide.
"""

from __future__ import annotations

import os
import re
import string
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml


# Built-in variable names that are always available.
BUILTIN_VARS = {
    "date",
    "time",
    "day_of_week",
    "week_number",
    "month",
    "year",
}


class TemplateError(Exception):
    """Raised for any templates-store failure.

    Covers missing files, missing required variables (strict mode),
    invalid YAML, missing ``body`` field, etc. The message is meant
    to be user-facing — keep it specific.
    """


@dataclass
class Template:
    """A single loaded template."""

    name: str
    body: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    default_vars: dict[str, str] = field(default_factory=dict)
    # Populated by load, not persisted.
    path: Path | None = field(default=None, compare=False)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict (matches the YAML file format)."""
        return {
            "name": self.name,
            "description": self.description,
            "body": self.body,
            "tags": list(self.tags),
            "default_vars": dict(self.default_vars),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Template":
        """Build a Template from a parsed YAML dict. Validates required fields."""
        if not isinstance(data, dict):
            raise TemplateError(
                f"Template must be a YAML mapping, got {type(data).__name__}"
            )
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            raise TemplateError("Template is missing required field 'name'")
        body = data.get("body")
        if not isinstance(body, str):
            raise TemplateError(
                f"Template '{name}' is missing required field 'body' "
                f"(must be a string, got {type(body).__name__})"
            )
        description = data.get("description", "") or ""
        if not isinstance(description, str):
            raise TemplateError(
                f"Template '{name}' field 'description' must be a string"
            )
        tags = data.get("tags", []) or []
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise TemplateError(
                f"Template '{name}' field 'tags' must be a list of strings"
            )
        default_vars = data.get("default_vars", {}) or {}
        if not isinstance(default_vars, dict) or not all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in default_vars.items()
        ):
            raise TemplateError(
                f"Template '{name}' field 'default_vars' must be a "
                f"mapping of string -> string"
            )
        return cls(
            name=name.strip(),
            description=description,
            body=body,
            tags=[t.strip() for t in tags],
            default_vars=dict(default_vars),
        )


def _builtin_vars(now: datetime | None = None) -> dict[str, str]:
    """Compute the built-in variable values for the current UTC moment."""
    now = now or datetime.now(timezone.utc)
    return {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "day_of_week": now.strftime("%A"),
        "week_number": f"{now.isocalendar().week:02d}",
        "month": now.strftime("%m"),
        "year": now.strftime("%Y"),
    }


# Regex for a ``{variable}`` placeholder: letters/digits/underscore, must
# start with a letter or underscore. Same rules as ``string.Template``'s
# default ``idpattern``, just wrapped in ``{ ... }`` instead of ``$ ...``.
_BRACE_IDPATTERN = r"(?a:[_a-z][_a-z0-9]*)"


class _BraceTemplate(string.Template):
    """``string.Template`` that uses ``{var}`` instead of ``$var``.

    Built-in ``string.Template`` accepts ``$var`` and ``${var}`` only.
    We override the regex ``pattern`` (not just ``delimiter``) because
    the upstream pattern treats ``}`` as a literal, which leaves a
    trailing brace in the rendered output. The overridden pattern below
    matches ``{varname}`` as a single token (consuming the closing
    brace). The base class compiles this in ``__init_subclass__`` so
    it must be a string at class-creation time.
    """

    delimiter = "{"
    idpattern = _BRACE_IDPATTERN

    pattern = r"""
    \{ (?:
        (?P<escaped>\{) |
        (?P<named>""" + _BRACE_IDPATTERN + r""")
    ) \}
    """


def _sanitize_name(name: str) -> str:
    """Sanitize a template name for use as a filename.

    Lowercase, replace any non-alphanumeric/dash/underscore with dash,
    collapse repeats, trim dashes.
    """
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9_-]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-_")
    if not s:
        raise TemplateError(f"Invalid template name: {name!r}")
    return s


class TemplatesStore:
    """File-backed store for post templates.

    Each template is a ``.yaml`` (or ``.yml``) file inside ``dir``.
    Filenames are sanitized versions of ``Template.name`` with a
    matching ``.yaml`` extension.

    Thread-safety: not concurrent-safe; serialize access with an
    external lock if you have multiple writers.
    """

    def __init__(self, dir: str | Path | None = None):
        self.dir = Path(
            dir
            or os.environ.get("LINKEDIN_MCP_TEMPLATES_DIR")
            or (Path.home() / ".linkedin-mcp" / "templates")
        )

    # -- I/O helpers -----------------------------------------------------

    def _ensure_dir(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, name: str) -> Path:
        """Resolve the on-disk path for a given template name.

        Tries the sanitized name first; if no file exists, falls back
        to a directory scan so renamed templates can still be looked up.
        """
        self._ensure_dir()
        for ext in (".yaml", ".yml"):
            candidate = self.dir / f"{_sanitize_name(name)}{ext}"
            if candidate.exists():
                return candidate
        return self.dir / f"{_sanitize_name(name)}.yaml"

    # -- CRUD ------------------------------------------------------------

    def list_templates(self) -> list[Template]:
        """Return every template found on disk, sorted by name."""
        self._ensure_dir()
        out: list[Template] = []
        for path in sorted(self.dir.glob("*.y*ml")):
            try:
                with path.open("r", encoding="utf-8") as fh:
                    data = yaml.safe_load(fh) or {}
                tpl = Template.from_dict(data)
                tpl.path = path
                out.append(tpl)
            except TemplateError:
                # Skip files that don't parse — caller can fix them.
                continue
            except yaml.YAMLError:
                continue
        return out

    def get(self, name: str) -> Template:
        """Load a single template by name. Raises TemplateError if missing."""
        self._ensure_dir()
        target = self._path_for(name)
        if not target.exists():
            raise TemplateError(
                f"Template {name!r} not found in {self.dir}. "
                f"Use 'linkedin-mcp templates list' to see available names."
            )
        try:
            with target.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except yaml.YAMLError as e:
            raise TemplateError(
                f"Template {name!r} has invalid YAML: {e}"
            ) from e
        tpl = Template.from_dict(data)
        tpl.path = target
        return tpl

    def save(self, tpl: Template) -> Template:
        """Persist a template. Returns the saved Template with ``path`` set.

        Accepts either a ``Template`` instance or a plain ``dict``
        matching the YAML schema (validated via ``Template.from_dict``).
        """
        if isinstance(tpl, dict):
            tpl = Template.from_dict(tpl)
        if not tpl.name:
            raise TemplateError("Template must have a non-empty name")
        self._ensure_dir()
        path = self._path_for(tpl.name)
        with path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(
                tpl.to_dict(),
                fh,
                sort_keys=False,
                allow_unicode=True,
                width=120,
            )
        tpl.path = path
        return tpl

    def exists(self, name: str) -> bool:
        """True if a template file exists for the given name."""
        self._ensure_dir()
        return self._path_for(name).exists()

    def delete(self, name: str) -> bool:
        """Remove a template by name.

        Raises ``TemplateError`` if the template does not exist. Returns
        True on successful deletion. (Use ``exists(name)`` first if you
        want a silent idempotent path.)
        """
        self._ensure_dir()
        path = self._path_for(name)
        if not path.exists():
            raise TemplateError(
                f"Cannot delete: template {name!r} not found in {self.dir}."
            )
        path.unlink()
        return True

    # -- Render ----------------------------------------------------------

    def render(
        self,
        name: str,
        variables: dict[str, Any] | None = None,
        *,
        vars: dict[str, Any] | None = None,  # alias for ``variables``
        strict: bool = False,
        now: datetime | None = None,
    ) -> str:
        """Load and render a template.

        Built-in variables are filled first, then ``default_vars`` from
        the template's YAML, then any caller-provided ``variables``
        (which override everything else). Missing keys in strict mode
        raise ``TemplateError``; missing keys in safe mode are left as
        ``{key}`` in the output.

        ``vars`` is accepted as an alias for ``variables`` for callers
        who prefer that name (the word ``vars`` shadows the builtin,
        so the public API uses ``variables``).
        """
        if variables is None and vars is not None:
            variables = vars
        tpl = self.get(name)
        merged: dict[str, Any] = {}
        merged.update(_builtin_vars(now=now))
        merged.update(tpl.default_vars)
        if variables:
            merged.update({str(k): ("" if v is None else str(v)) for k, v in variables.items()})
        tmpl = _BraceTemplate(tpl.body)
        if strict:
            # Catch missing keys so the user gets a clear error.
            try:
                return tmpl.substitute(merged)
            except KeyError as e:
                missing = e.args[0]
                raise TemplateError(
                    f"Template '{name}' is missing required variable {missing!r}. "
                    f"Pass --var {missing}=VALUE or add it to default_vars."
                ) from None
        return tmpl.safe_substitute(merged)

    # -- Bulk I/O --------------------------------------------------------

    def import_file(self, src: Path | str) -> list[Template]:
        """Import one or more templates from a YAML file.

        The file may contain either a single template mapping or a list
        of mappings under a top-level ``templates:`` key.
        """
        src = Path(src)
        if not src.exists():
            raise TemplateError(f"Source file not found: {src}")
        try:
            with src.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except yaml.YAMLError as e:
            raise TemplateError(
                f"Invalid YAML in {src}: {e}"
            ) from e
        items: Iterable[dict[str, Any]]
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict) and "templates" in data:
            items = data["templates"] or []
        else:
            items = [data]
        written: list[Template] = []
        for item in items:
            tpl = Template.from_dict(item)
            self.save(tpl)
            written.append(tpl)
        return written

    def export(self, name: str) -> str:
        """Return the YAML representation of a single template."""
        tpl = self.get(name)
        return yaml.safe_dump(tpl.to_dict(), sort_keys=False, allow_unicode=True, width=120)