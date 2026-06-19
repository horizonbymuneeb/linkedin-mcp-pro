# Troubleshooting

Common issues + fixes. If you don't see your problem, run `linkedin-mcp-health` first and share the output.

---

## Installation

### `pipx install` fails with PEP 668

You're using an externally-managed Python (Debian 12+, Ubuntu 23.04+, macOS 14+).

**Fix**: Use `pipx` (already installed as a separate venv manager), or use a venv:

```bash
python3 -m venv ~/.linkedin-mcp-venv
~/.linkedin-mcp-venv/bin/pip install linkedin-mcp-pro
```

### `pip install` fails with "No module named pip"

Python is too old (pre-3.4) or pip is missing.

**Fix**: Install Python 3.11+ via your OS package manager or [pyenv](https://github.com/pyenv/pyenv).

### `linkedin-mcp-health: command not found`

The CLI scripts weren't installed in your PATH.

**Fix**:

- pipx: `pipx ensurepath` then restart shell
- pip: `python3 -m site --user-base` → add `bin/` to PATH
- venv: source the venv's `activate`

---

## Web dashboard

### Port 8080 already in use

```bash
linkedin-mcp-web --port 8090
```

Or find and kill the conflicting process:

```bash
lsof -i :8080
```

### Pages return 404

You may be accessing an old `/static/X.html` URL. The dashboard uses clean URLs (`/jobs` not `/static/jobs.html`). Check your bookmarks.

### Pages load but look unstyled

The CSS failed to load. Check:

1. Browser DevTools → Network tab → look for failed `.css` requests
2. Console errors
3. Tailwind CDN may be blocked — try a different network

### Sidebar links don't work

You may have an old version. Update:

```bash
pipx upgrade linkedin-mcp-pro
```

### Browser login hangs at "Opening browser..."

Playwright browsers aren't installed.

**Fix**:

```bash
linkedin-mcp-install install-browsers
```

Or manually:

```bash
python3 -m playwright install chromium
```

---

## LinkedIn connection

### "Invalid cookies" / "Session expired"

`li_at` cookie expired (90-day max). Re-import:

```bash
linkedin-mcp-login
```

### "Challenge required" / "Verify it's you"

LinkedIn detected unusual activity. **Do not** automate your way around this — let the user solve it manually:

1. Open LinkedIn in your normal browser
2. Complete the challenge
3. Re-export cookies

### Login works but API calls fail with 401

Cookie validation fails on the server side. Check:

1. Are you using the right account? `--account company`
2. Is the cookie encrypted with a different passphrase? Re-encrypt with current one.
3. Has the cookie been corrupted in storage? Re-import.

---

## Posting

### "Safety gate blocked" on every post

Your caps are set too low. Check `/safety` page or:

```bash
linkedin-mcp-safety status
```

Common caps: 5 posts/day, 30 comments/day, 100 likes/day. Adjust in `/safety`.

### Post succeeds locally but doesn't appear on LinkedIn

LinkedIn API delay. Wait 60 seconds and refresh LinkedIn.

If still missing, check `/audit` for the actual response code.

### Post contains links → account flagged

LinkedIn aggressively flags new accounts that post links. Mitigation:

- Wait 2 weeks after account creation before posting links
- Mix link and non-link posts
- Use a domain you've "warmed up" (posted from browser first)

---

## Scheduling

### Scheduled post didn't fire

Check the worker is running:

```bash
linkedin-mcp-schedule worker --status
```

If not, start it:

```bash
linkedin-mcp-schedule worker --detach
```

Or restart the web server (worker starts with it).

### "Queue is empty" but I scheduled something

The schedule is in a different account's queue. Use:

```bash
linkedin-mcp-schedule list --account <name>
```

### Schedule fired but post is in "gate_failed" status

The safety gate blocked it. View the reason in `/audit` or:

```bash
linkedin-mcp-safety history --limit 5
```

---

## Jobs module

### "No CV uploaded"

Upload at `/jobs` → Setup tab. PDF or DOCX only.

### Match scores seem wrong

The keyword Jaccard scorer is fast but approximate. For more nuanced scoring, configure an LLM at `/llm`.

### Apply fails with "easy_apply_only" mismatch

The job doesn't support LinkedIn's Easy Apply. Either:

- Set `easy_apply_only=False` in jobs settings (will require manual finish in browser)
- Skip this job

### Cover letter looks generic

LLM is not configured. Add an LLM provider at `/llm` for personalized cover letters.

---

## AI agent integration

### "MCP server not found" in Claude Desktop

1. Check `linkedin-mcp-install verify claude-desktop`
2. Restart Claude Desktop (config changes need a restart)
3. Check `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or equivalent path

### Tools don't appear in Cursor

1. Restart Cursor
2. Settings → Models → MCP → confirm "linkedin-mcp-pro" is listed
3. Check Cursor's log: `Cmd/Ctrl+Shift+P` → "Show Logs"

### "Tool execution failed: not authenticated"

The agent doesn't have credentials. Either:

- Run the MCP server with auth: set `LINKEDIN_MCP_API_TOKEN`
- Or run on localhost (no auth needed)

---

## Performance

### Dashboard is slow to load

- Check disk: `df -h ~/.linkedin-mcp/` — should have >1GB free
- Check DB size: `du -sh ~/.linkedin-mcp/profile/state.db` — archive old audit logs
- Restart the worker: `linkedin-mcp-schedule worker --restart`

### High CPU usage

Usually the deadman watchdog or feed listener. Check:

```bash
ps aux | grep linkedin-mcp
```

Stop unneeded listeners via the engagement page.

### Out of memory

LinkedIn MCP Pro uses ~150MB baseline + ~50MB per browser. If you have many concurrent browser sessions, reduce with `--max-browsers 2`.

---

## Logs & diagnostics

```bash
# Live logs
linkedin-mcp-web 2>&1 | tee /tmp/linkedin-mcp.log

# System health
linkedin-mcp-health

# Detailed diagnostics
linkedin-mcp-doctor
```

For bug reports, include:

1. `linkedin-mcp-health` output
2. Last 50 lines of logs
3. Steps to reproduce
4. OS + Python version

---

## Getting help

- [GitHub Issues](https://github.com/horizonbymuneeb/linkedin-mcp-pro/issues)
- [Discussions](https://github.com/horizonbymuneeb/linkedin-mcp-pro/discussions)
- [Safety docs](safety.md) — read before reporting "I'm getting blocked"