# LinkedIn MCP Pro

> **LinkedIn automation for AI agents — 54 MCP tools, ban-safety gates, full web dashboard, MIT licensed.**

[![Tests](https://img.shields.io/badge/tests-721%2F721-10b981)](https://github.com/horizonbymuneeb/linkedin-mcp-pro)
[![Version](https://img.shields.io/badge/version-2.3.9-5e6ad2)](https://github.com/horizonbymuneeb/linkedin-mcp-pro/releases)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776ab)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-22c55e)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-live-0a66c2)](https://horizonbymuneeb.github.io/linkedin-mcp-pro/)

**LinkedIn MCP Pro** is a [Model Context Protocol](https://modelcontextprotocol.io/) server that gives any AI agent a complete LinkedIn workflow: compose, schedule, engage, search jobs, track applications, monitor account health — all behind safety gates that keep your account out of ban territory.

It ships with a unified **web dashboard** (15 pages, Linear + LinkedIn design system) and **10 CLI commands** for headless operation.

---

## ✨ Features

### For AI agents (MCP)
- **54 tools** across drafts, scheduling, engagement, jobs, analytics, safety, multi-account
- **stdio MCP server** — works with Claude Desktop, Cursor, Cline, Windsurf, Zed, Continue, Open WebUI, LibreChat, LobeChat
- **Ban-safety gates** built into every write operation
- **Multi-account** with per-account persona + rate limits

### For humans (Web UI)
- **15-page dashboard** with persistent sidebar + topbar shell
- **Linear dark mode** (`#5e6ad2` accent) + **LinkedIn content cards** (`#0a66c2` brand)
- **Jobs module** — CV upload, profile wizard, search, match scoring, cover letter, application tracker
- **Real-time analytics** — engagement heatmap, best-time-to-post, ab testing results
- **Mobile responsive** with collapsible sidebar

### For ops (CLI)
- `linkedin-mcp-web` — start the dashboard
- `linkedin-mcp-install` — wire any MCP host in one command
- `linkedin-mcp-templates` — manage post templates
- `linkedin-mcp-schedule` — cron + queue management
- `linkedin-mcp-stats` / `linkedin-mcp-health` — diagnostics
- `linkedin-mcp-login` — headless cookie import
- `linkedin-mcp-deadman` — watchdog for stalled automations
- `linkedin-mcp-analytics` — CSV / JSON export
- `linkedin-mcp-pro` — stdio MCP server

---

## 🚀 Quick start

=== "pipx (recommended)"

    ```bash
    pipx install git+https://github.com/horizonbymuneeb/linkedin-mcp-pro.git@v2.3.9
    linkedin-mcp-web --host 0.0.0.0 --port 8080
    ```
    Open <http://localhost:8080>

=== "pip"

    ```bash
    pip install --user git+https://github.com/horizonbymuneeb/linkedin-mcp-pro.git@v2.3.9
    linkedin-mcp-web
    ```

=== "From source"

    ```bash
    git clone https://github.com/horizonbymuneeb/linkedin-mcp-pro.git
    cd linkedin-mcp-pro
    pip install -e .
    linkedin-mcp-web
    ```

Then point your AI agent at the MCP server:

```json
{
  "mcpServers": {
    "linkedin-mcp-pro": {
      "command": "linkedin-mcp-pro",
      "args": ["serve"]
    }
  }
}
```

!!! tip "First-run checklist"
    1. Open the dashboard at `http://localhost:8080/connect`
    2. Log into LinkedIn (browser-driven cookie import — no password leaves your machine)
    3. Visit `/jobs` to upload your CV (or skip — drafts/scheduling work without it)
    4. Wire your agent via `/install`
    5. Compose your first draft via `/drafts`

---

## 🏗 Architecture at a glance

```
┌─────────────────┐     stdio JSON-RPC      ┌──────────────────┐
│  AI Agent Host  │ ◄─────────────────────► │  linkedin-mcp-pro │
│ (Claude/Cursor) │                          │   (FastAPI+CLI)   │
└─────────────────┘                          └────────┬───────────┘
                                                      │
                                  ┌───────────────────┼───────────────────┐
                                  ▼                   ▼                   ▼
                          ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
                          │ Web Dashboard│    │ SQLite DB    │    │ Browser Auto │
                          │  (15 pages)  │    │  (cookies,   │    │  (Playwright)│
                          │              │    │   drafts,    │    │              │
                          │              │    │   schedules) │    │              │
                          └──────────────┘    └──────────────┘    └──────────────┘
```

Read the full [architecture doc](getting-started/architecture.md).

---

## 📚 Documentation

<div class="grid cards" markdown>

-   :material-rocket-launch: **Getting started**

    ---

    [Install](getting-started/install.md) · [Quickstart](getting-started/quickstart.md) · [Architecture](getting-started/architecture.md) · [Agent setup](getting-started/agents.md)

-   :material-book-open-variant: **Guides**

    ---

    [Dashboard tour](guides/dashboard.md) · [Jobs module](guides/jobs.md) · [Drafts & templates](guides/drafts.md) · [Scheduling](guides/scheduling.md) · [Engagement](guides/engagement.md) · [Multi-account](guides/multi-account.md)

-   :material-cog: **Reference**

    ---

    [CLI commands](reference/cli/index.md) · [REST API](reference/api/index.md) · [MCP tools (54)](reference/mcp-tools/index.md)

-   :material-shield-check: **Operations**

    ---

    [Safety system](operations/safety.md) · [Proxy setup](operations/proxy-setup.md) · [Troubleshooting](operations/troubleshooting.md) · [Roadmap](operations/roadmap.md)

</div>

---

## 🛡 Safety guarantees

Every write path runs through a **ban-safety gate** that checks:

- Daily action caps (configurable per account)
- Velocity windows (no more than N actions per hour)
- Content patterns (LinkedIn spam triggers blocked)
- Duplicate detection (no identical posts within 7 days)
- Rate-limit backoff when LinkedIn throttles

The gate is **always on** — there is no `--unsafe` flag. See [safety.md](operations/safety.md) for the full policy.

---

## 🤝 Contributing

We welcome PRs! See [contributing.md](getting-started/contributing.md) for setup, style guide, and the test-first workflow.

---

## 📄 License

MIT © [horizonbymuneeb](https://github.com/horizonbymuneeb)