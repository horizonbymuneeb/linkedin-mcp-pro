# linkedin-mcp-pro

> **Open-source MCP server for LinkedIn.** Profiles, search, jobs, posts, connections, messages. Self-hosted, ban-safe, MIT licensed.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io)

---

## What is it?

linkedin-mcp-pro is a [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that exposes your LinkedIn account as 23 tools for any MCP-compatible client — while keeping you in control of *how* those tools act on your behalf.

**22 tools, organized in 3 groups:**

| Group | Tools | Backend | Ban risk |
|---|---|---|---|
| **Reads** (12) | profile lookup, search people/jobs/companies, feed, inbox, conversations, pending invitations | LinkedIn Voyager API (HTTP) | ⚪ None |
| **Writes** (10) | connection requests, posts, comments, reactions, messages, accept/decline/withdraw invitations | `agent-browser` CLI (Vercel Labs) | 🟢 Hardened with safety layer |
| **Stats** (2) | daily quota usage, audit log | Local SQLite | ⚪ None |

---

## Why use it instead of SaaS alternatives?

| | linkedin-mcp-pro | SaaS (e.g. Zopto, Lemlist) |
|---|---|---|
| **Cost** | Free (your time to host) | $59-300/mo |
| **Data** | Stays on your machine | Their servers |
| **Open source** | ✅ MIT (audit it) | ❌ Closed |
| **Self-hostable** | ✅ Docker / systemd / bare | ❌ |
| **Ban safety** | Built-in (warmup, jitter, business hours) | Their responsibility |
| **Rate limits** | You control (DB-enforced) | Their tier |
| **MCP integration** | Any MCP client (Claude Desktop, Cursor, Windsurf, etc.) | Their dashboard |

---

## Features

### 🛡️ Ban-safety (the focus)

- **Daily caps**, DB-enforced (e.g. 20 connections, 2 posts, 30 messages)
- **Warm-up mode**: Week 1: 5 conn/day, Week 2: 10, Week 3: 15, Week 4+: full caps
- **Business hours**: actions only run in your configured window (default 9-20 UTC, Mon-Fri)
- **Jitter**: 3-15 min random delay between actions (mimics human)
- **429 backoff**: exponential cooldown on rate-limit responses
- **CAPTCHA detection**: pauses all writes 24h, alerts you
- **Dry-run mode**: every write tool accepts `dry_run=true` to preview
- **Audit log**: every action recorded with timestamp, target, status, detail
- **Pause on quota-exhaust**: yellow zone (60%) warning, red (90%), exhausted (100%)

### 🔧 22 tools (full list)

**Reads (no ban risk)**
- `get_my_profile`, `get_person_profile`
- `search_people`, `search_jobs`, `search_companies`
- `get_job_details`, `get_company_profile`, `get_company_employees`
- `get_feed`, `get_inbox`, `get_conversation`
- `get_pending_invitations`

**Writes (safety-enforced)**
- `send_connection_request` (with optional personalized note)
- `create_post` (text + optional media URL)
- `delete_post`
- `comment_on_post`
- `react_to_post` (LIKE, CELEBRATE, INSIGHTFUL, etc.)
- `send_message`
- `accept_invitation`, `decline_invitation`, `withdraw_invitation`

**Stats**
- `get_daily_stats` (quota used/limit/zone per action)
- `get_audit_log` (recent actions with status)

### 📊 Storage

- **SQLite** for quotas, queue, audit log, session state
- **Browser profile** persisted at `data/browser-profile/` (Patchright)
- **No external DB** required
- **Retention**: audit log auto-pruned at 90 days (configurable)

---

## Installation

### Prerequisites

- Python 3.11+ (tested on 3.13)
- Node.js 20+ and npm (for `agent-browser` CLI)
- LinkedIn account with `li_at` cookie (see [Getting your cookie](#getting-your-li_at-cookie))

### Option A: pip install (recommended)

```bash
# 1. Install agent-browser (Rust CLI for write actions)
npm install -g agent-browser
agent-browser install --with-deps

# 2. Install linkedin-mcp-pro
git clone https://github.com/your-org/linkedin-mcp-pro
cd linkedin-mcp-pro
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
# Edit .env with your LI_AT (and optionally JSESSIONID)
linkedin-mcp-health
```

### Option B: Docker

```bash
git clone https://github.com/your-org/linkedin-mcp-pro
cd linkedin-mcp-pro
cp .env.example .env
# Edit .env with your LI_AT
docker compose up -d
docker compose logs -f linkedin-mcp-pro
```

### Option C: systemd (production)

See [`systemd/linkedin-mcp-pro.service`](systemd/linkedin-mcp-pro.service).

```bash
sudo cp systemd/linkedin-mcp-pro.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now linkedin-mcp-pro
sudo systemctl status linkedin-mcp-pro
```

---

## Configuration

All config is via environment variables (or `.env` file). See [`.env.example`](.env.example) for the full reference.

### Minimum (reads only)

```bash
LI_AT=your-li_at-value-here
```

### Recommended (reads + writes)

```bash
LI_AT=your-li_at-value-here
DAILY_LIMIT_CONNECTION_REQUESTS=20
DAILY_LIMIT_POSTS=2
BUSINESS_HOURS_START=9
BUSINESS_HOURS_END=20
WARMUP_ENABLED=true
```

### Production (use file-based secrets)

```bash
LI_AT_FILE=/etc/linkedin-mcp-pro/li_at
JSESSIONID_FILE=/etc/linkedin-mcp-pro/jsessionid
```

---

## Getting your `li_at` cookie

1. Open https://www.linkedin.com in Chrome/Firefox and log in
2. Open DevTools (F12 or Cmd+Opt+I)
3. Go to **Application** tab → **Cookies** → `https://www.linkedin.com`
4. Find the `li_at` cookie, double-click its value, copy
5. Paste into `.env` as `LI_AT=...`

For browser-based writes, you also need a working browser session — Patchright will use this cookie to bootstrap.

**Optional but recommended**: copy the `JSESSIONID` cookie too (improves API reliability).

---

## Usage with Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "linkedin": {
      "command": "uvx",
      "args": ["--from", "/absolute/path/to/linkedin-mcp-pro", "linkedin-mcp-pro"],
      "env": {
        "LI_AT_FILE": "/etc/linkedin-mcp-pro/li_at"
      }
    }
  }
}
```

Or if installed via `pip install -e .`:

```json
{
  "mcpServers": {
    "linkedin": {
      "command": "linkedin-mcp-pro",
      "env": {
        "LI_AT_FILE": "/etc/linkedin-mcp-pro/li_at"
      }
    }
  }
}
```

---

## Example: a daily workflow (Roman Urdu / English mix)

> "Mujhe 20 recruiters ko LinkedIn pe connect request bhejne hain, 1 post karo, aur 5 jobs search karo 'AI engineer' in San Francisco."

The MCP client will:
1. Call `search_jobs(keywords="AI engineer", location="San Francisco", limit=5)` (read)
2. Call `create_post(text="...", visibility="PUBLIC", dry_run=false)` (write, safety-checked)
3. Call `send_connection_request(public_id="recruiter1", note="...")` × 20 (writes, jittered, capped)

You can preview any write by passing `dry_run=true`:

> "Show me what the next connection request would look like."

→ `send_connection_request(public_id="recruiter1", note="...", dry_run=true)`

---

## Architecture

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design.

```
┌──────────────────────────────────────────────────┐
│  linkedin-mcp-pro  (Python 3.13)                 │
┌──────────────┐  ┌──────────────┐  ┌────────┐  │
│ Voyager API  │  │  agent-browser│  │ Safety │  │
│  (reads +    │  │  (writes:    │  │ Layer  │  │
│   fast data) │  │   connect/   │  │ +queue │  │
│              │  │   post/      │  │ +audit │  │
│              │  │   message)   │  │  log   │  │
└──────┬───────┘  └──────┬───────┘  └───┬────┘  │
│         │                 │              │       │
│         └────────┬────────┴──────────────┘       │
│                  ▼                                │
│  ┌──────────────────────────────────────┐       │
│  │  SQLite (./data/linkedin-mcp-pro.db)  │       │
│  │  - daily_quotas  - action_queue       │       │
│  │  - audit_log     - session_state      │       │
│  └──────────────────────────────────────┘       │
└──────────────────────────────────────────────────┘
```

---

## Safety in depth

See [`docs/SAFETY.md`](docs/SAFETY.md) for the full ban-prevention design.

The key insight: **rate limits are signals, not bugs.** If LinkedIn says "slow down", we *want* to slow down — not blast through. Every write tool goes through `SafetyGuard.enforce()` which checks:

1. **Business hours** — never outside your configured window
2. **Daily quota** — hard cap from DB, with warm-up ramp
3. **429 backoff** — exponential, with consecutive-count multiplier
4. **CAPTCHA** — never auto-resolve, hard pause 24h + alert
5. **Audit** — every action recorded, regardless of outcome

---

## Development

```bash
# Install with dev deps
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check linkedin_mcp/
ruff format linkedin_mcp/

# Type check
mypy linkedin_mcp/
```

### Project layout

```
linkedin-mcp-pro/
├── linkedin_mcp/
│   ├── __init__.py
│   ├── config.py        # env loading, validation
│   ├── db.py            # SQLite (quotas, queue, audit)
│   ├── safety.py        # SafetyGuard
│   ├── server.py        # MCP server, 22 tools
│   ├── cli.py           # health, stats commands
│   ├── api/             # Voyager HTTP client (reads)
│   ├── browser/         # agent-browser client (writes)
│   └── tools/           # (future) tool-specific helpers
├── data/                # runtime: db, browser profile
├── tests/
│   ├── test_api.py
│   ├── test_browser.py
│   ├── test_safety.py
│   └── test_db.py
├── docs/
│   ├── ARCHITECTURE.md
│   ├── SAFETY.md
│   └── CONTRIBUTING.md
├── examples/
│   └── claude_desktop_config.json
├── systemd/
│   └── linkedin-mcp-pro.service
├── pyproject.toml
├── .env.example
├── LICENSE              # MIT
└── README.md
```

---

## Contributing

We welcome PRs. See [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md) for guidelines.

**Especially wanted:**
- More test coverage (current focus: tools, safety)
- Documentation improvements
- LinkedIn Voyager endpoint discovery (the API is undocumented)
- New write tools (e.g. skill endorsement, post scheduling)

---

## ⚖️ Legal & Ethics

This tool automates a third-party service (LinkedIn). By using it:

- You agree to LinkedIn's [Terms of Service](https://www.linkedin.com/legal/user-agreement)
- You acknowledge that **automation may violate LinkedIn's TOS** and risk account restrictions
- You are solely responsible for your usage
- The authors disclaim all liability for account actions

**We do not encourage spam, unsolicited outreach, or any activity that violates LinkedIn's fair-use policies.** Use responsibly.

---

## License

[MIT](LICENSE) © 2026 linkedin-mcp-pro contributors
