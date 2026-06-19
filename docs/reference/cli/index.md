# CLI reference

LinkedIn MCP Pro ships with **10 CLI commands** for headless operation.

## Quick reference

| Command | Purpose |
|---------|---------|
| [`linkedin-mcp-pro`](#linkedin-mcp-pro) | Run the stdio MCP server (for AI agents) |
| [`linkedin-mcp-web`](#linkedin-mcp-web) | Start the web dashboard |
| [`linkedin-mcp-install`](#linkedin-mcp-install) | Wire an MCP host + manage accounts |
| [`linkedin-mcp-login`](#linkedin-mcp-login) | Headless cookie import |
| [`linkedin-mcp-templates`](#linkedin-mcp-templates) | Manage post templates |
| [`linkedin-mcp-schedule`](#linkedin-mcp-schedule) | Manage scheduled posts |
| [`linkedin-mcp-analytics`](#linkedin-mcp-analytics) | Export analytics (CSV / JSON) |
| [`linkedin-mcp-stats`](#linkedin-mcp-stats) | Show account statistics |
| [`linkedin-mcp-health`](#linkedin-mcp-health) | Diagnostics + readiness check |
| [`linkedin-mcp-deadman`](#linkedin-mcp-deadman) | Watchdog for stalled automations |

---

## linkedin-mcp-pro

Run the stdio MCP server. **This is what AI agents launch.**

```bash
linkedin-mcp-pro serve
```

Options:

| Flag | Default | Description |
|------|---------|-------------|
| `--transport` | `stdio` | `stdio` (default) or `sse` |
| `--port` | `8081` | Port for SSE transport |

Exit codes:

- `0` — clean shutdown
- `1` — fatal error (see logs)
- `130` — interrupted (Ctrl+C)

---

## linkedin-mcp-web

Start the dashboard.

```bash
linkedin-mcp-web [--host HOST] [--port PORT] [--reload]
```

| Flag | Default | Env var | Description |
|------|---------|---------|-------------|
| `--host` | `127.0.0.1` | `LINKEDIN_MCP_WEB_HOST` | Bind host |
| `--port` | `8080` | `LINKEDIN_MCP_WEB_PORT` | Bind port |
| `--reload` | off | — | Auto-reload on file changes (dev) |

Examples:

```bash
# Local only
linkedin-mcp-web

# LAN-accessible
linkedin-mcp-web --host 0.0.0.0 --port 8080

# Dev mode with auto-reload
linkedin-mcp-web --reload
```

---

## linkedin-mcp-install

Wire an MCP host (Claude Desktop, Cursor, etc.) + manage accounts.

### Subcommands

#### `add <agent>`

Add LinkedIn MCP Pro to an MCP host.

```bash
linkedin-mcp-install add claude-desktop
linkedin-mcp-install add cursor
linkedin-mcp-install add cline
linkedin-mcp-install add windsurf
linkedin-mcp-install add zed
linkedin-mcp-install add continue
linkedin-mcp-install add open-webui
linkedin-mcp-install add librechat
linkedin-mcp-install add lobechat
```

Supported agents: `claude-desktop-mac`, `claude-desktop-linux`, `claude-desktop-win`, `claude-code`, `cursor`, `cline`, `continue`, `windsurf`, `zed`, `open-webui`, `librechat`, `lobechat`.

#### `verify <agent>`

Check if a host is correctly wired.

```bash
linkedin-mcp-install verify claude-desktop
```

#### `print-configs`

Print the JSON snippet for every supported agent (useful for manual setup).

```bash
linkedin-mcp-install print-configs
```

#### `account <action>`

Manage accounts.

```bash
linkedin-mcp-install account add
linkedin-mcp-install account list
linkedin-mcp-install account default <name>
linkedin-mcp-install account pause <name>
linkedin-mcp-install account resume <name>
linkedin-mcp-install account remove <name>
```

---

## linkedin-mcp-login

Headless cookie import.

```bash
linkedin-mcp-login                # interactive
linkedin-mcp-login --li-at X      # paste cookie value
linkedin-mcp-login --account X    # save to specific account
```

---

## linkedin-mcp-templates

```bash
linkedin-mcp-templates list
linkedin-mcp-templates add --name "Hiring post" --body "..."
linkedin-mcp-templates update --id X --body "..."
linkedin-mcp-templates delete --id X
linkedin-mcp-templates export --format json > templates.json
linkedin-mcp-templates import templates.json
```

---

## linkedin-mcp-schedule

```bash
linkedin-mcp-schedule add --draft-id X --at "2026-01-15T14:00:00Z"
linkedin-mcp-schedule list
linkedin-mcp-schedule cancel --id X
linkedin-mcp-schedule worker                       # foreground worker
linkedin-mcp-schedule worker --detach              # background worker
```

---

## linkedin-mcp-analytics

```bash
linkedin-mcp-analytics summary --days 30
linkedin-mcp-analytics posts --format csv > posts.csv
linkedin-mcp-analytics engagement --format json
linkedin-mcp-analytics best-times                 # shows heatmap
```

---

## linkedin-mcp-stats

Show account statistics (followers, engagement, top posts).

```bash
linkedin-mcp-stats
linkedin-mcp-stats --days 7
linkedin-mcp-stats --format json
```

---

## linkedin-mcp-health

Diagnostics + readiness check.

```bash
linkedin-mcp-health
```

Output:

```
linkedin-mcp-pro v2.3.1 — ready
Python 3.11+
DB: ~/.linkedin-mcp/profile/state.db
Browser: available
Cookies: present (default account, expires in 87 days)
LLM keys: 3 configured
Scheduler worker: running (PID 12345)
```

Exit codes:

- `0` — healthy
- `1` — warning (some subsystem degraded)
- `2` — error (something broken)

---

## linkedin-mcp-deadman

Watchdog for stalled automations.

```bash
linkedin-mcp-deadman check                     # one-shot check
linkedin-mcp-deadman watch                     # foreground watch
linkedin-mcp-deadman watch --interval 5m       # custom interval
```

Alerts via:

- Console (always)
- Telegram (if `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` set)
- Webhook (if `LINKEDIN_MCP_DEADMAN_WEBHOOK` set)

Triggers:

- No posts fired in 24h (when scheduled posts exist)
- No comments/likes in 24h (when engagement enabled)
- Scheduler worker not running
- Cookies expired or invalid