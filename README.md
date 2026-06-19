# LinkedIn MCP Pro

> **LinkedIn automation for AI agents — 54 MCP tools, ban-safety gates, full web dashboard, MIT licensed.**

[![Tests](https://img.shields.io/badge/tests-721%20passing-10b981)](https://github.com/horizonbymuneeb/linkedin-mcp-pro)
[![Version](https://img.shields.io/badge/version-2.3.2-5e6ad2)](https://github.com/horizonbymuneeb/linkedin-mcp-pro/releases)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776ab)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-22c55e)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-live-0a66c2)](https://horizonbymuneeb.github.io/linkedin-mcp-pro/)

**[📚 Full documentation](https://horizonbymuneeb.github.io/linkedin-mcp-pro/)** · **[Quickstart](https://horizonbymuneeb.github.io/linkedin-mcp-pro/getting-started/quickstart/)** · **[API reference](https://horizonbymuneeb.github.io/linkedin-mcp-pro/reference/api/)** · **[MCP tools](https://horizonbymuneeb.github.io/linkedin-mcp-pro/reference/mcp-tools/)**

---

## What is it?

**LinkedIn MCP Pro** is a [Model Context Protocol](https://modelcontextprotocol.io/) server that gives any AI agent a complete LinkedIn workflow:

- ✍️ **Compose** posts (manual / AI / template)
- 📅 **Schedule** posts via cron-like queue
- 💬 **Engage** with comments, DMs, mentions
- 🔍 **Search** jobs with match scoring + auto cover letter
- 📊 **Analytics** with engagement heatmaps + A/B testing
- 🛡 **Ban-safety gates** on every write path

It ships with a unified **web dashboard** (15 pages) and **10 CLI commands**.

---

## ⚡ Quick start

```bash
# Install
pipx install git+https://github.com/horizonbymuneeb/linkedin-mcp-pro.git@v2.3.2

# Start dashboard
linkedin-mcp-web --host 0.0.0.0 --port 8080

# Or run as stdio MCP server for AI agents
linkedin-mcp-pro serve
```

Open <http://localhost:8080> for the dashboard, or wire your agent:

```bash
linkedin-mcp-install add claude-desktop    # or cursor, cline, windsurf, zed, ...
```

Full guide: **[Quickstart](https://horizonbymuneeb.github.io/linkedin-mcp-pro/getting-started/quickstart/)**

---

## 📊 What's inside

| Surface | Count |
|---------|-------|
| **MCP tools** | 54 (across 10 families) |
| **REST endpoints** | 60+ |
| **CLI commands** | 10 |
| **Dashboard pages** | 15 (unified Linear+LinkedIn design system) |
| **Tests** | 721 passing |

---

## 🖼 Dashboard preview

The dashboard at `/` is a unified shell — same sidebar, topbar, design tokens across every page:

- **Workspace**: Home, Drafts, Schedule, Engage, **Jobs**, Analytics
- **Account**: Connect, Cookies, Profile
- **Configure**: LLM, Safety, Audit, Install, Settings, Templates

Design system: **Linear dark mode** (`#5e6ad2` accent) + **LinkedIn content cards** (`#0a66c2` brand).

See [Dashboard tour](https://horizonbymuneeb.github.io/linkedin-mcp-pro/guides/dashboard/) for a walkthrough.

---

## 🤖 Agent support

Works with any MCP host:

- Claude Desktop (macOS / Linux / Windows)
- Claude Code
- Cursor
- Cline (VS Code)
- Continue.dev
- Windsurf
- Zed
- Open WebUI
- LibreChat
- LobeChat

Full setup: [Agent setup guide](https://horizonbymuneeb.github.io/linkedin-mcp-pro/getting-started/agents/)

---

## 🛡 Safety

Every write path runs through a **ban-safety gate**:

- Daily action caps (per-account configurable)
- Velocity windows (no more than N actions per hour)
- Content pattern checks (LinkedIn spam triggers blocked)
- Duplicate detection (no identical posts within 7 days)
- Rate-limit backoff when LinkedIn throttles

The gate is **always on** — there is no `--unsafe` flag. See [safety.md](https://horizonbymuneeb.github.io/linkedin-mcp-pro/operations/safety/).

---

## 📦 What ships

```
linkedin-mcp-pro/
├── linkedin_mcp/                # Core package
│   ├── static/                  # 16 HTML pages + unified shell
│   ├── jobs/                    # Jobs module (CV, search, apply, tracker)
│   ├── tools/                   # 17 tool modules
│   ├── cli*.py                  # 10 CLI entry points
│   └── web.py                   # FastAPI server
├── tests/                       # 721 tests
├── scripts/
│   └── e2e_test.py             # Full E2E smoke test (64 checks)
├── docs/                        # MkDocs Material documentation
├── mkdocs.yml                   # Docs build config
├── install.sh / install.ps1     # One-line installers
└── README.md                    # This file
```

---

## 🧪 Development

```bash
git clone https://github.com/horizonbymuneeb/linkedin-mcp-pro.git
cd linkedin-mcp-pro
pip install -e .
pytest tests/                    # 721 tests
python scripts/e2e_test.py       # E2E smoke (64 checks)

# Build docs locally
pip install mkdocs mkdocs-material pymdown-extensions
mkdocs serve                     # → http://127.0.0.1:8000
```

---

## 📄 License

MIT © [horizonbymuneeb](https://github.com/horizonbymuneeb)

See [LICENSE](LICENSE).

---

## 🔗 Links

- 📚 [Documentation](https://horizonbymuneeb.github.io/linkedin-mcp-pro/)
- 🐛 [Issue tracker](https://github.com/horizonbymuneeb/linkedin-mcp-pro/issues)
- 💬 [Discussions](https://github.com/horizonbymuneeb/linkedin-mcp-pro/discussions)
- 🚀 [Releases](https://github.com/horizonbymuneeb/linkedin-mcp-pro/releases)