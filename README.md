# linkedin-mcp-pro

> **54 MCP tools** for LinkedIn automation — posting, search, engagement, analytics, scheduling, and an 11-panel **Tailwind+Alpine** web UI.

[![Tests](https://img.shields.io/badge/tests-685%20passing-brightgreen)]()
[![MCP Tools](https://img.shields.io/badge/MCP%20tools-54-blue)]()
[![Web Panels](https://img.shields.io/badge/web%20panels-11-purple)]()
[![Version](https://img.shields.io/badge/version-2.0.0-orange)]()

---

## ✨ What's new in v2.0

- **🎨 Web UI Redesign** — 11 panels migrated to **Tailwind CSS + Alpine.js** (CDN, no build step). Dark/light mode, mobile responsive, gradient hero cards, Inter font, sticky nav.
- **🛠 Install Wizard** — New `linkedin-mcp-install` CLI: `doctor`, `detect`, `add`, `remove`, `print-configs`, `verify` for 6 agents.
- **🔑 LLM API Key Management** — 6 providers (OpenAI, Anthropic, OpenRouter, NVIDIA, Custom, Pool) with masked keys, test connection, base_url override.
- **🛡 Ban-Safety Gate** — Daily limits, business hours, whitelist/blacklist, account age minimums, warmup multipliers. `dry_run=true` is the default.
- **🤖 Auto-Engagement** — `auto_like`, `auto_comment`, `auto_connect` with dry-run + warmup.
- **📜 Audit Log** — 50-row scrollable table with action/status/date filters and a live SSE indicator.
- **⚡ Real-time SSE** for live log streaming.

**Stats:** 685 tests passing · 54 MCP tools · 21 web endpoints · 9 CLI commands · 11 web panels.

See [CHANGELOG.md](CHANGELOG.md) for the full v2.0 entry.

---

## 🖼 UI Preview (11 Tailwind+Alpine panels)

All panels are static HTML served from `/static/{name}.html` — no build step required.

| # | Panel | File | Description |
|---|---|---|---|
| 1 | Dashboard | [`static/dashboard.html`](static/dashboard.html) | KPI overview, live activity feed, quick actions |
| 2 | LLM Keys | [`static/llm.html`](static/llm.html) | 6 provider cards, add modal, masked key display, test connection |
| 3 | Safety | [`static/safety.html`](static/safety.html) | Ban-safety gate: limits, hours, whitelist/blacklist, warmup |
| 4 | Engagement | [`static/engagement.html`](static/engagement.html) | auto_like / auto_comment / auto_connect with dry-run |
| 5 | Audit | [`static/audit.html`](static/audit.html) | 50-row scrollable table, action/status/date filters, live SSE |
| 6 | Schedules | [`static/schedules.html`](static/schedules.html) | Cron-style post scheduler with preview |
| 7 | Templates | [`static/templates.html`](static/templates.html) | Reusable post templates with variable substitution |
| 8 | Drafts | [`static/drafts.html`](static/drafts.html) | Post drafts with autosave and publish-now |
| 9 | Analytics | [`static/analytics.html`](static/analytics.html) | Impressions, reactions, profile views (charts) |
| 10 | Install | [`static/install.html`](static/install.html) | One-click install for 6 agents |
| 11 | Profile | [`static/profile.html`](static/profile.html) | Your LinkedIn profile snapshot |

### Web UI features
- 🌗 **Dark / light / system** theme (no flash, localStorage + OS preference)
- 📱 **Mobile responsive** (1 / 2 / 3 column grids)
- 🎨 **Gradient hero cards** (LinkedIn blue + accent colors)
- 🔤 **Inter** font, smooth 200ms transitions
- 📌 **Sticky top nav** with theme toggle
- ⚡ **SSE** for live log + audit feed

Screenshots are auto-generated — see [docs/SCREENSHOTS.md](docs/SCREENSHOTS.md).

---

## 🚀 Install

```bash
pip install linkedin-mcp-pro
# or from source
git clone https://github.com/horizonbymuneeb/linkedin-mcp-pro
cd linkedin-mcp-pro
pip install -e .
```

Then run the install wizard:

```bash
linkedin-mcp-install doctor   # health check
linkedin-mcp-install detect   # find installed agents
linkedin-mcp-install add claude-desktop
```

Or use the full CLI:

```bash
linkedin-mcp --help
```

---

## 🧰 CLI

```
linkedin-mcp            # start the MCP server
linkedin-mcp-install    # interactive install wizard
  ├─ doctor             # health check (Python, profile, cookies, MCP, SOCKS, IP, LinkedIn login)
  ├─ detect             # auto-detect installed agents
  ├─ add <agent>        # register MCP server in agent config
  ├─ remove <agent>     # clean uninstall
  ├─ print-configs      # JSON snippets for manual paste
  └─ verify <agent>     # confirm installation
```

Supported agents: **Claude Desktop, Cursor, Continue, Cline, Windsurf, Codex**.

---

## 🔌 MCP Tools (54 total)

| Category | Tools |
|---|---|
| **Profile** | `get_profile`, `update_profile`, `get_profile_views` |
| **Posts** | `create_post`, `delete_post`, `get_posts`, `repost`, `react_to_post` |
| **Search** | `search_people`, `search_jobs`, `search_companies`, `search_posts` |
| **Engagement** | `like_post`, `comment_on_post`, `send_connection_request`, `send_message` |
| **Auto** | `auto_like`, `auto_comment`, `auto_connect` *(v1.1.0, dry-run + warmup)* |
| **Analytics** | `get_post_analytics`, `get_profile_analytics`, `get_engagement_stats` |
| **Schedules** | `schedule_post`, `list_schedules`, `cancel_schedule` |
| **Templates** | `create_template`, `render_template`, `list_templates` |
| **Drafts** | `save_draft`, `list_drafts`, `publish_draft` |
| **Safety** | `get_safety_config`, `set_safety_config`, `check_action` |
| **LLM** | `add_llm_key`, `list_llm_keys`, `test_llm_key`, `remove_llm_key` *(v1.1.0)* |
| **Audit** | `get_audit_log`, `export_audit_log` |
| **Misc** | login helpers, cookie refresh, proxy/SOCKS, health checks |

See [`docs/MCP_TOOLS.md`](docs/MCP_TOOLS.md) for the full schema.

---

## 🌐 Web Endpoints (21)

```
GET  /                       # landing / dashboard redirect
GET  /static/{panel}.html    # 11 panels (Tailwind+Alpine, no build)
GET  /api/llm/providers      # list configured LLM providers
POST /api/llm/providers      # add a provider
POST /api/llm/providers/{id}/test
DELETE /api/llm/providers/{id}
GET  /api/safety             # get safety config
POST /api/safety             # update safety config
GET  /api/audit              # 50-row audit feed
GET  /api/audit/stream       # SSE live log
GET  /api/engagement/{action}/dry-run
POST /api/engagement/{action}
GET  /api/posts, /api/drafts, /api/templates, /api/schedules
GET  /api/profile, /api/analytics, /api/health
```

---

## 🛡 Safety & Security

- **Voyager API is BANNED** for posting — we use only web scraping via Playwright.
- Daily limits enforced, business hours respected, account age minimum 30 days.
- 14-day warmup × 0.2x multiplier for new accounts.
- `dry_run=true` is the default — opt in to live mode.

See [SECURITY.md](SECURITY.md) for the full policy and how to report vulnerabilities.

---

## 🧪 Testing

```bash
pip install -e .[test]
pytest                 # 685 tests
pytest --cov=linkedin_mcp
```

---

## 📜 License

MIT © horizonbymuneeb
