# Quickstart

Get from zero to your first automated LinkedIn post in **under 5 minutes**.

## 1. Install (30 seconds)

```bash
pipx install git+https://github.com/horizonbymuneeb/linkedin-mcp-pro.git@v2.3.1
```

Verify:

```bash
linkedin-mcp-health
```

Expected output:

```text
linkedin-mcp-pro v2.3.1 — ready
Python 3.11+
DB: ~/.linkedin-mcp/profile/state.db
Browser: available
```

## 2. Start the dashboard (5 seconds)

```bash
linkedin-mcp-web --host 0.0.0.0 --port 8080
```

Open <http://localhost:8080>.

## 3. Connect LinkedIn (60 seconds)

Open <http://localhost:8080/connect> and pick a method:

=== "Browser (recommended)"

    1. Click **Launch browser**
    2. A Playwright window opens — log into LinkedIn normally
    3. Once the dashboard shows your profile, you're connected
    4. Cookies are saved to `~/.linkedin-mcp/profile/cookies.json` (encrypted at rest with your passphrase)

=== "Paste li_at cookie"

    1. Open LinkedIn in your own browser
    2. DevTools → Application → Cookies → `li_at` → copy value
    3. Paste it in the **li_at** field on the connect page
    4. Click **Save**

## 4. Compose your first draft (45 seconds)

Open <http://localhost:8080/drafts>.

The composer has three modes:

| Mode | What it does | When to use |
|------|--------------|-------------|
| **Manual** | Rich-text editor with live preview | When you want full control |
| **AI assist** | LLM drafts from a 1-line prompt | When you're out of ideas |
| **Template** | Pick from saved post templates | When you have a repeatable pattern |

Write your post, hit **Save draft**, then **Post now** (subject to safety gates).

## 5. (Optional) Wire an AI agent

```bash
linkedin-mcp-install add claude-desktop    # or: cursor, cline, windsurf, zed, ...
```

Restart your agent. It will discover 54 new MCP tools automatically.

## 6. (Optional) Try the jobs module

Open <http://localhost:8080/jobs>.

1. **Setup** tab → upload your CV (PDF or DOCX)
2. **Profile** tab → fill out skills, target roles, salary range
3. **Search** tab → click **Find jobs** (uses LinkedIn search with your filters)
4. Review match scores → click **Apply** on ones you like → cover letter auto-generated
5. Track status in **Tracker** tab

## What now?

- [Architecture overview](architecture.md) — understand how the pieces fit
- [Safety system](../operations/safety.md) — read this before going autonomous
- [Dashboard tour](../guides/dashboard.md) — every page explained
- [MCP tools reference](../reference/mcp-tools/index.md) — full tool inventory