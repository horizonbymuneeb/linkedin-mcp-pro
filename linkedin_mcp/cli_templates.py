"""`linkedin-mcp templates` subcommand (v0.5.0).

Subcommands::

    list                    — table of all templates
    show <name>             — print body + vars of one template
    render <name>           — render with --var key=value (repeatable)
                             add --post to publish via create_post MCP tool
    new                     — open $EDITOR with a YAML skeleton
    delete <name>           — remove a template (with confirmation)
    import <file>           — bulk-import from a YAML file
    export <name>           — dump a template's YAML to stdout

The rendering path is intentionally decoupled from the MCP server — you can
render templates offline, in tests, or in a CI pipeline. ``--post`` is the
only subcommand that requires a live server / browser profile.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

from .templates import BUILTIN_VARS, Template, TemplateError, TemplatesStore


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _build_table(rows: list[Template]) -> str:
    """Format templates as a fixed-width table."""
    if not rows:
        return "(no templates — run `linkedin-mcp templates new` to create one)"
    name_w = max(len("NAME"), max(len(t.name) for t in rows))
    desc_w = max(len("DESCRIPTION"), max(len(t.description) for t in rows))
    tag_w = max(len("TAGS"), max(len(", ".join(t.tags)) for t in rows))
    lines = [
        f"{'NAME':<{name_w}}  {'DESCRIPTION':<{desc_w}}  {'TAGS':<{tag_w}}",
        f"{'-' * name_w}  {'-' * desc_w}  {'-' * tag_w}",
    ]
    for t in rows:
        lines.append(
            f"{t.name:<{name_w}}  {t.description:<{desc_w}}  "
            f"{', '.join(t.tags):<{tag_w}}"
        )
    return "\n".join(lines)


def cmd_list(args: argparse.Namespace) -> int:
    store = TemplatesStore()
    rows = store.list_templates()
    print(_build_table(rows))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    store = TemplatesStore()
    try:
        tpl = store.get(args.name)
    except TemplateError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    print(f"# {tpl.name}")
    if tpl.description:
        print(f"# {tpl.description}")
    print(f"# tags: {', '.join(tpl.tags) if tpl.tags else '(none)'}")
    print(f"# path: {tpl.path}")
    if tpl.default_vars:
        print(f"# default_vars:")
        for k, v in tpl.default_vars.items():
            print(f"#   {k} = {v}")
    print(f"# built-in vars available: {', '.join(sorted(BUILTIN_VARS))}")
    print("---")
    print(tpl.body)
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    store = TemplatesStore()
    try:
        rendered = store.render(args.name, args.vars, strict=args.strict)
    except TemplateError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1

    if args.post:
        # Wire into the live MCP server. Falls back to a direct call to
        # linkedin_mcp.browser.create_post() if the server is reachable,
        # otherwise instruct the user how to use the rendered text.
        try:
            import asyncio
            from .config import load_config
            from .db import DB
            from .safety import ActionPlan, DryRun, SafetyGuard

            cfg = load_config()
            db = DB(cfg.storage.db_path)
            guard = SafetyGuard(cfg, db)
            plan = ActionPlan(
                action="post",
                target="self",
                payload={"text": rendered[:100], "template": args.name},
                dry_run=args.dry_run,
            )
            guard.enforce(plan)
            if args.dry_run:
                db.close()
                print(f"✓ {plan}")
                return 0
            try:
                from . import browser as _br
                result = asyncio.run(
                    _br.create_post(text=rendered, visibility="PUBLIC")
                )
            except ImportError:
                # Browser module not built / agent-browser missing.
                # Fall back to printing the rendered text + instructions.
                print(
                    "⚠️  Browser module unavailable — printing rendered text instead.",
                    file=sys.stderr,
                )
                print(rendered)
                db.close()
                return 0
            guard.record_success(plan, result=result)
            db.close()
            print(f"✓ Posted via template '{args.name}'.")
            print(f"  {result}")
            return 0
        except Exception as e:
            print(f"❌ Post failed: {e}", file=sys.stderr)
            print("\nRendered text (copy/paste manually):", file=sys.stderr)
            print("---")
            print(rendered)
            return 1

    # Default: print to stdout.
    print(rendered)
    return 0


SKELETON_TEMPLATE = """\
name: {name}
description: A short one-line description of when to use this
tags:
  - example
  - draft
default_vars:
  body: "What I shipped this week"
body: |
  Happy {day_of_week}!

  {body}

  Posted via linkedin-mcp-pro on {date}.
"""


def cmd_new(args: argparse.Namespace) -> int:
    """Open $EDITOR with a YAML skeleton; save on exit."""
    editor = os.environ.get("EDITOR", "vi").strip() or "vi"
    name = args.name or "my-template"
    skeleton = SKELETON_TEMPLATE.format(name=name)
    tmp = Path("/tmp") / f"linkedin-mcp-template-{name}.yaml"
    tmp.write_text(skeleton, encoding="utf-8")

    import subprocess
    rc = subprocess.call([editor, str(tmp)])
    if rc != 0:
        print(f"❌ Editor exited with code {rc}; nothing saved.", file=sys.stderr)
        return rc or 1

    # Validate before saving.
    try:
        import yaml as _yaml
        data = _yaml.safe_load(tmp.read_text(encoding="utf-8"))
    except Exception as e:  # pragma: no cover - defensive
        print(f"❌ Could not parse edited file: {e}", file=sys.stderr)
        return 1

    if not isinstance(data, dict) or not data.get("name"):
        print(
            "❌ Edited file is missing a 'name' field — nothing saved.",
            file=sys.stderr,
        )
        return 1

    store = TemplatesStore()
    try:
        tpl = store.save(data)
    except TemplateError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1

    print(f"✓ Saved template '{tpl.name}' → {tpl.path}")
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    store = TemplatesStore()
    if not args.yes:
        try:
            tpl = store.get(args.name)
        except TemplateError as e:
            print(f"❌ {e}", file=sys.stderr)
            return 1
        ans = input(
            f"Delete template '{tpl.name}' at {tpl.path}? [y/N] "
        ).strip().lower()
        if ans not in ("y", "yes"):
            print("Aborted.", file=sys.stderr)
            return 0

    try:
        store.delete(args.name)
    except TemplateError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    print(f"✓ Deleted template '{args.name}'.")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    store = TemplatesStore()
    try:
        imported = store.import_file(args.file)
    except TemplateError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    for t in imported:
        print(f"✓ Imported '{t.name}'")
    print(f"\n{len(imported)} template(s) imported into {store.dir}.")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    store = TemplatesStore()
    try:
        text = store.export(args.name)
    except TemplateError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    sys.stdout.write(text)
    return 0


# ---------------------------------------------------------------------------
# argparse plumbing
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="linkedin-mcp templates",
        description="Manage reusable LinkedIn post templates.",
    )
    sub = p.add_subparsers(dest="subcommand", required=True)

    p_list = sub.add_parser("list", help="List all templates.")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="Show one template's body + vars.")
    p_show.add_argument("name", help="Template name.")
    p_show.set_defaults(func=cmd_show)

    p_render = sub.add_parser(
        "render", help="Render a template with --var key=value pairs.",
    )
    p_render.add_argument("name", help="Template name.")
    p_render.add_argument(
        "--var",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Variable substitution, repeatable.",
    )
    p_render.add_argument(
        "--strict",
        action="store_true",
        help="Fail (non-zero exit) if any {var} is missing.",
    )
    p_render.add_argument(
        "--post",
        action="store_true",
        help="Publish the rendered text via the MCP create_post tool.",
    )
    p_render.add_argument(
        "--dry-run",
        action="store_true",
        help="With --post: plan only, don't actually post.",
    )
    p_render.set_defaults(func=cmd_render)

    p_new = sub.add_parser("new", help="Create a new template via $EDITOR.")
    p_new.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Template name (will be sanitized for the filename).",
    )
    p_new.set_defaults(func=cmd_new)

    p_del = sub.add_parser("delete", help="Delete a template.")
    p_del.add_argument("name", help="Template name.")
    p_del.add_argument(
        "-y", "--yes", action="store_true", help="Skip confirmation prompt."
    )
    p_del.set_defaults(func=cmd_delete)

    p_imp = sub.add_parser("import", help="Bulk-import templates from YAML.")
    p_imp.add_argument("file", help="Path to a YAML file (list or single doc).")
    p_imp.set_defaults(func=cmd_import)

    p_exp = sub.add_parser("export", help="Export a template's YAML to stdout.")
    p_exp.add_argument("name", help="Template name.")
    p_exp.set_defaults(func=cmd_export)

    return p


def _parse_vars(items: Sequence[str]) -> dict[str, str]:
    """Turn ['k1=v1', 'k2=v2'] into {'k1': 'v1', 'k2': 'v2'}."""
    out: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"--var expects KEY=VALUE, got: {item!r}")
        k, _, v = item.partition("=")
        out[k.strip()] = v
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Coerce --var entries into a dict.
    if hasattr(args, "var") and args.var:
        try:
            args.vars = _parse_vars(args.var)
        except ValueError as e:
            print(f"❌ {e}", file=sys.stderr)
            return 2

    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
