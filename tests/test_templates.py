"""Unit tests for the post-templates feature (v0.5.0).

Covers ``TemplatesStore`` plus the CLI and MCP tool wrappers. Uses
``tmp_path`` + a per-test monkeypatch of ``LINKEDIN_MCP_TEMPLATES_DIR``
so tests are fully isolated from the real user's templates.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from linkedin_mcp import cli_templates
from linkedin_mcp.templates import (
    BUILTIN_VARS,
    Template,
    TemplateError,
    TemplatesStore,
    _builtin_vars,
    _sanitize_name,
)
from linkedin_mcp.tools import templates as tpl_tools


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TemplatesStore:
    """A store rooted in a per-test tmp dir (no ~/.linkedin-mcp pollution)."""
    monkeypatch.setenv("LINKEDIN_MCP_TEMPLATES_DIR", str(tmp_path))
    return TemplatesStore(dir=tmp_path)


def _ns(
    subcommand: str,
    name: str | None = None,
    file: str | None = None,
) -> argparse.Namespace:
    """Build a minimal argparse.Namespace for direct cmd_*() calls.

    ``name`` is used by subcommands that take a positional template name
    (show / render / delete / export). ``file`` is used by ``import``.
    ``args.file`` is read off the NS by cmd_import; everything else reads
    ``args.name``.
    """
    ns = argparse.Namespace()
    ns.subcommand = subcommand
    if name is not None:
        ns.name = name
    if file is not None:
        ns.file = file
    # Defaults that several subcommands read off the NS:
    ns.vars = getattr(ns, "vars", {})
    ns.var = getattr(ns, "var", [])
    ns.strict = False
    ns.post = False
    ns.dry_run = False
    ns.yes = False
    return ns


def _make_now() -> datetime:
    return datetime(2026, 6, 17, 14, 32, 8, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Sanity / utility tests
# ---------------------------------------------------------------------------


def test_sanitize_name_basic() -> None:
    assert _sanitize_name("Weekly Update") == "weekly-update"
    assert _sanitize_name("foo_bar") == "foo_bar"
    assert _sanitize_name("  Foo / Bar  ") == "foo-bar"
    assert _sanitize_name("My Cool!!! Template (v2)") == "my-cool-template-v2"


def test_sanitize_name_invalid_raises() -> None:
    with pytest.raises(TemplateError):
        _sanitize_name("")
    with pytest.raises(TemplateError):
        _sanitize_name("!!!")
    with pytest.raises(TemplateError):
        _sanitize_name("///")


def test_builtin_vars_have_expected_keys() -> None:
    vars_ = _builtin_vars(now=_make_now())
    assert set(vars_) == BUILTIN_VARS
    assert vars_["date"] == "2026-06-17"
    assert vars_["time"] == "14:32:08"
    assert vars_["day_of_week"] == "Wednesday"
    # 2026-06-17 is ISO week 25
    assert vars_["week_number"] == "25"
    assert vars_["month"] == "06"
    assert vars_["year"] == "2026"


# ---------------------------------------------------------------------------
# Store: empty / save / list
# ---------------------------------------------------------------------------


def test_list_empty(store: TemplatesStore) -> None:
    assert store.list_templates() == []


def test_save_and_list(store: TemplatesStore) -> None:
    tpl = Template(
        name="weekly-update",
        description="Monday wrap-up",
        body="Happy {day_of_week}! Shipped: {summary}",
        tags=["weekly", "monday"],
        default_vars={"summary": "(nothing yet)"},
    )
    saved = store.save(tpl)
    assert saved.path is not None
    assert saved.path.exists()
    assert saved.path.name == "weekly-update.yaml"

    rows = store.list_templates()
    assert [r.name for r in rows] == ["weekly-update"]
    assert rows[0].tags == ["weekly", "monday"]
    assert rows[0].description == "Monday wrap-up"


def test_save_accepts_dict(store: TemplatesStore) -> None:
    store.save({
        "name": "from-dict",
        "body": "Hello {who}",
        "tags": ["greeting"],
        "default_vars": {"who": "world"},
    })
    assert store.exists("from-dict")
    assert store.get("from-dict").default_vars == {"who": "world"}


def test_get_missing_raises(store: TemplatesStore) -> None:
    with pytest.raises(TemplateError) as ei:
        store.get("nope")
    assert "not found" in str(ei.value)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_render_basic(store: TemplatesStore) -> None:
    store.save(Template(name="t1", body="Hi {name}!"))
    out = store.render("t1", vars={"name": "Ada"})
    assert out == "Hi Ada!"


def test_render_uses_default_vars(store: TemplatesStore) -> None:
    store.save(Template(
        name="t2",
        body="Welcome {user}, you have {count} messages.",
        default_vars={"user": "stranger", "count": "0"},
    ))
    out = store.render("t2")
    assert out == "Welcome stranger, you have 0 messages."


def test_caller_vars_override_defaults(store: TemplatesStore) -> None:
    store.save(Template(
        name="t3",
        body="Hello {user}",
        default_vars={"user": "stranger"},
    ))
    out = store.render("t3", vars={"user": "Alice"})
    assert out == "Hello Alice"


def test_render_builtin_vars(
    store: TemplatesStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.save(Template(
        name="dated",
        body="Posted on {date} ({day_of_week}), week {week_number} of {year}, {time} UTC",
    ))
    out = store.render("dated", now=_make_now())
    assert out == (
        "Posted on 2026-06-17 (Wednesday), week 25 of 2026, 14:32:08 UTC"
    )


def test_render_safe_missing_warns(store: TemplatesStore) -> None:
    """strict=False (default): missing vars are left verbatim, no exception."""
    store.save(Template(name="t4", body="Hi {name}, your code is {code}"))
    out = store.render("t4", vars={"name": "Bob"})
    assert "Hi Bob" in out
    assert "{code}" in out  # left as-is, not replaced


def test_render_strict_missing_raises(store: TemplatesStore) -> None:
    """strict=True: missing var -> TemplateError naming the missing key."""
    store.save(Template(name="t5", body="Hi {name}"))
    with pytest.raises(TemplateError) as ei:
        store.render("t5", strict=True)
    msg = str(ei.value)
    assert "missing required variable" in msg.lower()
    assert "'name'" in msg or '"name"' in msg


def test_render_strict_passes_when_all_provided(store: TemplatesStore) -> None:
    store.save(Template(name="t6", body="Hi {name}"))
    out = store.render("t6", vars={"name": "Z"}, strict=True)
    assert out == "Hi Z"


def test_render_coerces_none_vars_to_empty_string(store: TemplatesStore) -> None:
    store.save(Template(name="t7", body="User: {user}"))
    out = store.render("t7", vars={"user": None})  # type: ignore[arg-type]
    assert out == "User: "


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_delete(store: TemplatesStore) -> None:
    store.save(Template(name="to-delete", body="x"))
    assert store.exists("to-delete")
    assert store.delete("to-delete") is True
    assert not store.exists("to-delete")


def test_delete_missing_raises(store: TemplatesStore) -> None:
    with pytest.raises(TemplateError):
        store.delete("ghost")


# ---------------------------------------------------------------------------
# Validation / error paths
# ---------------------------------------------------------------------------


def test_invalid_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file with broken YAML surfaces a TemplateError, not a crash."""
    monkeypatch.setenv("LINKEDIN_MCP_TEMPLATES_DIR", str(tmp_path))
    bad = tmp_path / "broken.yaml"
    bad.write_text("name: oops\nbody: |:\n  bad indent : : :\n  :: !!\n")
    s = TemplatesStore(dir=tmp_path)
    with pytest.raises(TemplateError) as ei:
        s.get("broken")
    assert "invalid yaml" in str(ei.value).lower()


def test_missing_required_field_in_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A YAML file missing 'body' should produce a clear TemplateError.

    The on-disk filename is the source of truth for ``get(name)`` — the
    ``name:`` inside the YAML is metadata. So we look up the file by its
    sanitized filename.
    """
    monkeypatch.setenv("LINKEDIN_MCP_TEMPLATES_DIR", str(tmp_path))
    (tmp_path / "only-name.yaml").write_text("name: only-name\n")
    s = TemplatesStore(dir=tmp_path)
    with pytest.raises(TemplateError) as ei:
        s.get("only-name")
    assert "body" in str(ei.value).lower()


def test_save_validates_required_fields(store: TemplatesStore) -> None:
    with pytest.raises(TemplateError):
        store.save({"name": "", "body": "x"})
    with pytest.raises(TemplateError):
        store.save({"name": "ok", "body": 123})  # body must be str


def test_save_rejects_non_string_default_vars(store: TemplatesStore) -> None:
    with pytest.raises(TemplateError):
        store.save({"name": "x", "body": "y", "default_vars": {"k": 5}})


def test_save_rejects_non_list_tags(store: TemplatesStore) -> None:
    with pytest.raises(TemplateError):
        store.save({"name": "x", "body": "y", "tags": "weekly"})


def test_template_error_messages_are_descriptive(store: TemplatesStore) -> None:
    """Errors should name the offending template + field when possible."""
    try:
        store.save({"name": "broken", "body": 99})
    except TemplateError as e:
        msg = str(e)
        assert "broken" in msg
        assert "body" in msg
    else:
        pytest.fail("expected TemplateError")


# ---------------------------------------------------------------------------
# Import / export
# ---------------------------------------------------------------------------


def test_import_export_single_doc(
    store: TemplatesStore, tmp_path: Path
) -> None:
    src = tmp_path / "one.yaml"
    src.write_text(
        "name: imported-1\n"
        "body: Hi {name}\n"
        "tags: [t1]\n"
    )
    imported = store.import_file(src)
    assert [t.name for t in imported] == ["imported-1"]
    text = store.export("imported-1")
    assert "name: imported-1" in text
    assert "Hi {name}" in text


def test_import_list_doc(store: TemplatesStore, tmp_path: Path) -> None:
    src = tmp_path / "many.yaml"
    src.write_text(
        "- name: a\n"
        "  body: A {x}\n"
        "- name: b\n"
        "  body: B {x}\n"
        "  tags: [b-tag]\n"
    )
    imported = store.import_file(src)
    assert [t.name for t in imported] == ["a", "b"]
    assert store.get("b").tags == ["b-tag"]


def test_import_invalid_raises(store: TemplatesStore, tmp_path: Path) -> None:
    src = tmp_path / "bad.yaml"
    src.write_text("[unclosed")
    with pytest.raises(TemplateError) as ei:
        store.import_file(src)
    assert "invalid yaml" in str(ei.value).lower()


def test_import_empty_raises(store: TemplatesStore, tmp_path: Path) -> None:
    src = tmp_path / "empty.yaml"
    src.write_text("")
    with pytest.raises(TemplateError):
        store.import_file(src)


def test_export_missing_raises(store: TemplatesStore) -> None:
    with pytest.raises(TemplateError):
        store.export("does-not-exist")


# ---------------------------------------------------------------------------
# CLI surface (subcommand handlers, not argparse wiring)
# ---------------------------------------------------------------------------


def test_cli_list_renders_table(
    store: TemplatesStore, capsys: pytest.CaptureFixture[str]
) -> None:
    store.save(Template(name="x", description="X desc", body="x", tags=["t"]))
    rc = cli_templates.cmd_list(_ns("list"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "x" in out
    assert "X desc" in out
    assert "t" in out


def test_cli_list_empty(
    store: TemplatesStore, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli_templates.cmd_list(_ns("list"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "no templates" in out.lower()


def test_cli_show_missing_returns_one(
    store: TemplatesStore, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli_templates.cmd_show(_ns("show", "ghost"))
    assert rc == 1
    assert "not found" in capsys.readouterr().err.lower()


def test_cli_show_ok(
    store: TemplatesStore, capsys: pytest.CaptureFixture[str]
) -> None:
    store.save(Template(name="shown", body="Body {x}", description="d", tags=["t"]))
    rc = cli_templates.cmd_show(_ns("show", "shown"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Body {x}" in out
    assert "shown" in out


def test_cli_render_to_stdout(
    store: TemplatesStore, capsys: pytest.CaptureFixture[str]
) -> None:
    store.save(Template(name="hi", body="Hello {who}"))
    ns = _ns("render", "hi")
    ns.vars = {"who": "world"}
    rc = cli_templates.cmd_render(ns)
    assert rc == 0
    assert capsys.readouterr().out.strip() == "Hello world"


def test_cli_render_strict_missing_returns_one(
    store: TemplatesStore, capsys: pytest.CaptureFixture[str]
) -> None:
    store.save(Template(name="strict-r", body="Hi {who}"))
    ns = _ns("render", "strict-r")
    ns.vars = {}
    ns.strict = True
    rc = cli_templates.cmd_render(ns)
    assert rc == 1
    assert "missing" in capsys.readouterr().err.lower()


def test_cli_export_writes_to_stdout(
    store: TemplatesStore, capsys: pytest.CaptureFixture[str]
) -> None:
    store.save(Template(name="ex", body="Body"))
    rc = cli_templates.cmd_export(_ns("export", "ex"))
    assert rc == 0
    assert "name: ex" in capsys.readouterr().out


def test_cli_import_calls_store(
    store: TemplatesStore, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "src.yaml"
    src.write_text("name: imp\nbody: Hi {who}\n")
    rc = cli_templates.cmd_import(_ns("import", file=str(src)))
    assert rc == 0
    assert store.exists("imp")


def test_cli_delete_with_yes_flag(
    store: TemplatesStore, capsys: pytest.CaptureFixture[str]
) -> None:
    store.save(Template(name="kill-me", body="x"))
    ns = _ns("delete", "kill-me")
    ns.yes = True
    rc = cli_templates.cmd_delete(ns)
    assert rc == 0
    assert not store.exists("kill-me")


def test_cli_main_routes_to_subcommand(
    store: TemplatesStore, capsys: pytest.CaptureFixture[str]
) -> None:
    store.save(Template(name="route", body="x", description="d", tags=["t"]))
    rc = cli_templates.main(["list"])
    assert rc == 0
    assert "route" in capsys.readouterr().out


def test_cli_main_bad_var_format_returns_two(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = cli_templates.main(["render", "anything", "--var", "no-equals-sign"])
    assert rc == 2
    assert "KEY=VALUE" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# MCP tool wrappers (templates.py under linkedin_mcp/tools/)
# ---------------------------------------------------------------------------


def test_tools_list_templates(store: TemplatesStore) -> None:
    store.save(Template(name="m1", body="b", tags=["x"]))
    rows = tpl_tools.list_templates()
    assert any(r["name"] == "m1" for r in rows)
    assert all("body_length" in r for r in rows)


def test_tools_get_template(store: TemplatesStore) -> None:
    store.save(Template(name="mget", body="hello {w}", tags=["x"]))
    data = tpl_tools.get_template("mget")
    assert data["name"] == "mget"
    assert data["body"] == "hello {w}"


def test_tools_render_template(store: TemplatesStore) -> None:
    store.save(Template(name="mr", body="Hi {name}"))
    assert tpl_tools.render_template("mr", {"name": "AI"}) == "Hi AI"


def test_tools_save_template(store: TemplatesStore) -> None:
    out = tpl_tools.save_template(
        name="saved-by-tool",
        body="body {x}",
        description="from tool",
        tags=["t1", "t2"],
        default_vars={"x": "1"},
    )
    assert out["name"] == "saved-by-tool"
    assert store.exists("saved-by-tool")
    assert store.get("saved-by-tool").default_vars == {"x": "1"}


def test_tools_delete_template(store: TemplatesStore) -> None:
    store.save(Template(name="d", body="x"))
    out = tpl_tools.delete_template("d")
    assert out == {"deleted": True, "name": "d"}
    assert not store.exists("d")


def test_tools_save_rejects_empty_name(store: TemplatesStore) -> None:
    with pytest.raises(ValueError):
        tpl_tools.save_template(name="", body="x")


def test_tools_get_missing_raises_value_error(store: TemplatesStore) -> None:
    with pytest.raises(ValueError):
        tpl_tools.get_template("does-not-exist")


def test_tools_strict_render_raises(store: TemplatesStore) -> None:
    store.save(Template(name="strict-t", body="Hi {name}"))
    with pytest.raises(ValueError):
        tpl_tools.render_template("strict-t", {}, strict=True)
