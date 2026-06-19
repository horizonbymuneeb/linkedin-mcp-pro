"""Command-line entry point for the LinkedIn MCP install wizard."""

from __future__ import annotations

import json
import sys
from typing import Any

import click

from .installer_helpers import (
    AGENT_CONFIGS,
    build_config_snippet,
    detect_installed_agents,
    doctor_report,
    install_to_agent,
    is_installed,
    resolve_config_path,
    uninstall_from_agent,
)


def _print_table(rows: list[tuple[str, ...]], headers: tuple[str, ...]) -> None:
    widths = [max(len(str(row[i])) for row in [headers, *rows]) for i in range(len(headers))]
    line = " | ".join(str(h).ljust(widths[i]) for i, h in enumerate(headers))
    sep = "-+-".join("-" * w for w in widths)
    click.echo(line)
    click.echo(sep)
    for row in rows:
        click.echo(" | ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)))


@click.group()
def cli() -> None:
    """LinkedIn MCP installer."""


@cli.command("doctor")
def cmd_doctor() -> None:
    """Print environment diagnostics."""
    report = doctor_report()
    rows = [
        ("python_version", report["python_version"]),
        ("platform", report["platform"]),
        ("python_ok", str(report["python_ok"])),
        ("profile_dir", report["profile_dir"]),
        ("profile_dir_exists", str(report["profile_dir_exists"])),
        ("li_at_cookie_present", str(report["li_at_cookie_present"])),
    ]
    _print_table(rows, headers=("check", "value"))
    click.echo("")
    click.echo("Detected agents:")
    for name, installed in report["agents_detected"].items():
        mark = "✓" if installed else "✗"
        click.echo(f"  {mark} {name}")


@cli.command("detect")
def cmd_detect() -> None:
    """Detect which MCP agents are installed locally."""
    for name, installed in detect_installed_agents().items():
        mark = "✓" if installed else "✗"
        click.echo(f"{mark} {name}")


@cli.command("add")
@click.argument("agent_name")
def cmd_add(agent_name: str) -> None:
    """Install LinkedIn MCP into AGENT_NAME's config."""
    result = install_to_agent(agent_name)
    if result["ok"]:
        click.echo(f"Installed into {agent_name} at {result['path']}")
    else:
        click.echo(f"ERROR: {result.get('error', 'unknown')}", err=True)
        sys.exit(1)


@cli.command("remove")
@click.argument("agent_name")
def cmd_remove(agent_name: str) -> None:
    """Remove LinkedIn MCP from AGENT_NAME's config."""
    result = uninstall_from_agent(agent_name)
    if result["ok"]:
        if result.get("removed"):
            click.echo(f"Removed from {agent_name}")
        else:
            click.echo(f"No entry to remove for {agent_name}")
    else:
        click.echo(f"ERROR: {result.get('error', 'unknown')}", err=True)
        sys.exit(1)


@cli.command("print-configs")
def cmd_print_configs() -> None:
    """Print the config snippet for every supported agent."""
    for name, cfg in AGENT_CONFIGS.items():
        snippet = build_config_snippet(name)
        click.echo(f"### {cfg['display_name']}")
        click.echo("```json")
        click.echo(json.dumps(snippet, indent=2))
        click.echo("```")
        click.echo("")


@cli.command("verify")
@click.argument("agent_name")
def cmd_verify(agent_name: str) -> None:
    """Verify the install for AGENT_NAME."""
    if is_installed(agent_name):
        click.echo(f"OK: {agent_name} has the LinkedIn MCP entry at {resolve_config_path(agent_name)}")
    else:
        click.echo(f"MISSING: {agent_name} does not have the LinkedIn MCP entry")
        sys.exit(1)


@cli.command("list")
def cmd_list() -> None:
    """List all supported agents."""
    rows = [
        (name, cfg["display_name"], ",".join(cfg["os_support"]))
        for name, cfg in AGENT_CONFIGS.items()
    ]
    _print_table(rows, headers=("id", "display_name", "os"))


def main() -> Any:
    """Entry point registered in pyproject."""
    return cli()


if __name__ == "__main__":  # pragma: no cover
    main()
