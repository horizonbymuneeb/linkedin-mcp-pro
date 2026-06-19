# Installation Guide

## One-line install (macOS / Linux)

```bash
curl -fsSL https://raw.githubusercontent.com/horizonbymuneeb/linkedin-mcp-pro/main/install.sh | bash
```

## One-line install (Windows PowerShell)

```powershell
iwr https://raw.githubusercontent.com/horizonbymuneeb/linkedin-mcp-pro/main/install.ps1 | iex
```

Both scripts will:

1. Verify Python ≥ 3.11
2. Install `linkedin-mcp-pro` (via `pipx` if available, otherwise `pip --user`)
3. Create `~/.linkedin-mcp/profile/`
4. Print the config snippet for the most common agents on your OS

---

## Manual install

```bash
# one-liner (recommended)
curl -fsSL https://raw.githubusercontent.com/horizonbymuneeb/linkedin-mcp-pro/main/install.sh | bash

# or manual install from GitHub:
python3 -m pip install --user "git+https://github.com/horizonbymuneeb/linkedin-mcp-pro.git@v2.0.0"
# or with pipx (recommended):
pipx install "git+https://github.com/horizonbymuneeb/linkedin-mcp-pro.git@v2.0.0"
```

Then make sure `~/.local/bin` is on your `PATH`:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
```

Verify:

```bash
linkedin-mcp-install doctor
```

---

## Agent setup

Pick the agent(s) you use and run the matching `add` command. The installer
merges the LinkedIn MCP entry into the agent's config without disturbing
existing servers. Run `linkedin-mcp-install list` to see all supported agents.

### Claude Desktop (macOS)

```bash
linkedin-mcp-install add claude-desktop-mac
```

Config: `~/Library/Application Support/Claude/claude_desktop_config.json`

### Claude Desktop (Linux)

```bash
linkedin-mcp-install add claude-desktop-linux
```

Config: `~/.config/Claude/claude_desktop_config.json`

### Claude Desktop (Windows)

```powershell
linkedin-mcp-install add claude-desktop-win
```

Config: `%APPDATA%\Claude\claude_desktop_config.json`

### Claude Code

```bash
linkedin-mcp-install add claude-code
```

Config: `~/.claude.json`

### Cursor

```bash
linkedin-mcp-install add cursor
```

Config: `~/.cursor/mcp.json`. Restart Cursor after install.

### Cline (VS Code)

```bash
linkedin-mcp-install add cline
```

Config: `~/.cline/mcp_settings.json`.

### Continue.dev

```bash
linkedin-mcp-install add continue
```

Config: `~/.continue/config.json`.

### Windsurf

```bash
linkedin-mcp-install add windsurf
```

Config: `~/.codeium/windsurf/mcp_config.json`.

### Zed

```bash
linkedin-mcp-install add zed
```

Config: `~/.config/zed/settings.json` (uses `context_servers` — handled
automatically).

### Open WebUI

```bash
linkedin-mcp-install add open-webui
```

Config: `~/.open-webui/mcp_servers.json`.

### LibreChat

```bash
linkedin-mcp-install add librechat
```

Config: `~/.librechat/librechat.yaml`.

### LobeChat

```bash
linkedin-mcp-install add lobechat
```

Config: `~/.lobe-chat/mcp.json`.

---

## Cookie setup

LinkedIn's API requires a valid `li_at` session cookie from a logged-in
browser. Get yours with our helper:

<https://horizonbymuneeb.github.io/linkedin-mcp-pro/static/cookies.html>

Then export it before launching the server:

```bash
export LI_AT="<paste your li_at cookie here>"
```

To persist it, add the line to your shell rc file (`~/.bashrc`, `~/.zshrc`,
or the Windows equivalent).

---

## Verification

Run the built-in doctor:

```bash
linkedin-mcp-install doctor
```

You should see:

- `python_version` ≥ 3.11
- `profile_dir_exists` = `True`
- `li_at_cookie_present` = `True` (after you export it)
- A ✓ next to every agent config that exists on disk

To verify a specific agent:

```bash
linkedin-mcp-install verify cursor
```

If the entry is missing, re-run `linkedin-mcp-install add <agent>` and
restart the host application.

---

## HTTP API

When you run the LinkedIn MCP server with HTTP enabled, the install wizard
is also exposed under `/api/install`:

- `GET  /api/install/doctor`
- `GET  /api/install/agents`
- `GET  /api/install/agents/{agent}/config`
- `POST /api/install/install/{agent}?dry_run=false`
- `DELETE /api/install/uninstall/{agent}`
- `GET  /api/install/verify/{agent}`

---

## Uninstall

```bash
linkedin-mcp-install remove <agent>   # remove the MCP entry
pipx uninstall linkedin-mcp-pro        # remove the package
```
