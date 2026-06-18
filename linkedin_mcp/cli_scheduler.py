"""CLI subcommand for the post scheduler."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .config import load_config
from .db import DB
from .scheduler import PostScheduler, Schedule, SchedulerError


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "schedule",
        help="Manage scheduled posts",
        description=(
            "Schedule posts via YAML, enqueue to DB, worker drains them. "
            "YAML lives at ~/.linkedin-mcp/schedule.yaml by default."
        ),
    )
    sp = p.add_subparsers(dest="subcommand", required=True)
    sp.add_parser("list", help="List all schedules")
    show_p = sp.add_parser("show", help="Show a single schedule's YAML")
    show_p.add_argument("name")
    sp.add_parser(
        "run-due",
        help="Enqueue all due schedules into the DB action queue",
    )
    add_p = sp.add_parser("add", help="Add a new schedule (interactive or --from-yaml)")
    add_p.add_argument("--from-yaml", help="Path to a YAML file containing a single schedule")
    add_p.add_argument("--name", help="Schedule name (required for inline use)")
    add_p.add_argument("--cron", help="5-field cron expression (UTC)")
    add_p.add_argument("--at", help="Specific ISO-8601 datetime (one-shot)")
    add_p.add_argument("--days", nargs="+", help="Days of week (mon tue wed ...)")
    add_p.add_argument("--time", help="HH:MM (UTC)")
    add_p.add_argument("--template", help="Template name to render")
    add_p.add_argument("--text", help="Direct post text")
    add_p.add_argument("--var", action="append", default=[], help="template var key=value (repeatable)")
    add_p.add_argument("--tag", action="append", default=[])
    rm = sp.add_parser("remove", help="Remove a schedule")
    rm.add_argument("name")
    en = sp.add_parser("enable", help="Re-enable a disabled schedule")
    en.add_argument("name")
    dis = sp.add_parser("disable", help="Disable a schedule")
    dis.add_argument("name")
    p.set_defaults(func=cmd_main)


def cmd_main(args: argparse.Namespace) -> int:
    cmd = args.subcommand
    if cmd == "list":
        return cmd_list(args)
    if cmd == "show":
        return cmd_show(args)
    if cmd == "add":
        return cmd_add(args)
    if cmd == "remove":
        return cmd_remove(args)
    if cmd == "enable":
        return cmd_enable(args, enable=True)
    if cmd == "disable":
        return cmd_enable(args, enable=False)
    if cmd == "run-due":
        return cmd_run_due(args)
    print(f"❌ Unknown subcommand: {cmd}", file=sys.stderr)
    return 2


def _sched() -> PostScheduler:
    return PostScheduler()


def _db() -> DB:
    try:
        cfg = load_config()
        return DB(cfg.storage.db_path)
    except Exception as e:
        print(f"❌ Could not load config: {e}", file=sys.stderr)
        return DB(Path("./data/linkedin-mcp-pro.db"))


def _print_table(rows: list[tuple[str, ...]], headers: list[str]) -> None:
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(line)
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(c.ljust(widths[i]) for i, c in enumerate(r)))


def cmd_list(args: argparse.Namespace) -> int:
    s = _sched()
    schedules = s.list_schedules()
    if not schedules:
        print("No schedules yet. Add one with: linkedin-mcp schedule add --name foo ...")
        return 0
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    rows: list[tuple[str, ...]] = []
    for sc in schedules:
        nxt = s.next_run(sc, now=now)
        nxt_s = nxt.isoformat(timespec="seconds") if nxt else "-"
        rows.append((
            sc.name,
            "yes" if sc.enabled else "no",
            sc.cron or sc.at or f"{','.join(sc.days) or '*'}@{sc.time or '*'}",
            nxt_s,
            ",".join(sc.tags) or "-",
        ))
    _print_table(rows, ["NAME", "ENABLED", "WHEN", "NEXT RUN", "TAGS"])
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    s = _sched()
    try:
        sc = s.get(args.name)
    except SchedulerError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    import yaml
    print(yaml.safe_dump(sc.to_dict(), sort_keys=False, allow_unicode=True, width=120))
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    s = _sched()
    if args.from_yaml:
        with open(args.from_yaml, "r", encoding="utf-8") as fh:
            import yaml
            data = yaml.safe_load(fh) or {}
        if isinstance(data, list):
            if not data:
                print("❌ YAML file is empty", file=sys.stderr)
                return 1
            data = data[0]
        try:
            sc = Schedule.from_dict(data)
        except SchedulerError as e:
            print(f"❌ {e}", file=sys.stderr)
            return 1
    else:
        if not args.name:
            print("❌ --name is required (or use --from-yaml)", file=sys.stderr)
            return 1
        vars_dict: dict[str, str] = {}
        for v in args.var:
            if "=" not in v:
                print(f"❌ --var must be key=value, got {v!r}", file=sys.stderr)
                return 2
            k, val = v.split("=", 1)
            vars_dict[k] = val
        try:
            sc = Schedule.from_dict(
                {
                    "name": args.name,
                    "cron": args.cron,
                    "at": args.at,
                    "days": args.days or [],
                    "time": args.time,
                    "template": args.template,
                    "text": args.text,
                    "vars": vars_dict,
                    "tags": args.tag or [],
                    "enabled": True,
                }
            )
        except SchedulerError as e:
            print(f"❌ {e}", file=sys.stderr)
            return 1
    try:
        s.add(sc)
    except SchedulerError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    print(f"✅ Added schedule {sc.name!r}")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    s = _sched()
    if s.remove(args.name):
        print(f"✅ Removed schedule {args.name!r}")
        return 0
    print(f"❌ No schedule named {args.name!r}", file=sys.stderr)
    return 1


def cmd_enable(args: argparse.Namespace, *, enable: bool) -> int:
    s = _sched()
    try:
        sc = s.enable(args.name) if enable else s.disable(args.name)
    except SchedulerError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    state = "enabled" if enable else "disabled"
    print(f"✅ Schedule {sc.name!r} is now {state}")
    return 0


def cmd_run_due(args: argparse.Namespace) -> int:
    s = _sched()
    db = _db()
    ids = s.enqueue_due(db)
    print(f"✅ Enqueued {len(ids)} due post(s) into the action queue")
    if ids:
        print("  IDs:", ", ".join(str(i) for i in ids))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="linkedin-mcp-schedule")
    subparsers = parser.add_subparsers(dest="subcommand")
    register(subparsers)
    args = parser.parse_args(argv)
    return cmd_main(args)