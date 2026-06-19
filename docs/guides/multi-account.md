# Multi-account

Manage multiple LinkedIn accounts from one installation — useful for personal + company pages, agency work, or managing clients.

## Setup

```bash
linkedin-mcp-install account add
# Prompts for: account name, cookies (or launch browser)
```

This creates a new account entry in `~/.linkedin-mcp/profile/accounts/`.

## Listing accounts

```bash
linkedin-mcp-install account list
```

Output:

```
NAME          COOKIES  STATUS    LAST USED
personal      ✓        active    2 min ago
company       ✓        active    1 hour ago
client-acme   ✓        paused    3 days ago
```

## Switching accounts

### Per-action (recommended)

```bash
linkedin-mcp-schedule add --draft-id 123 --account company
```

### Default account

```bash
linkedin-mcp-install account default personal
```

The default is used when `--account` is omitted.

### In the dashboard

Sidebar footer → account switcher dropdown.

## Per-account config

Each account has its own:

| Setting | Where |
|---------|-------|
| Cookies | `~/.linkedin-mcp/profile/accounts/{name}/cookies.json` |
| Drafts | `~/.linkedin-mcp/profile/accounts/{name}/drafts/` |
| Schedules | `~/.linkedin-mcp/profile/accounts/{name}/schedules/` |
| Templates | Shared (templates are account-agnostic) |
| Safety caps | Per-account overrides possible |
| Persona (for AI drafts) | Per-account |
| Profile (for jobs module) | Per-account |

## Per-account safety

Each account has its own safety profile:

```bash
linkedin-mcp-safety set-caps --account personal \
    --posts-per-day 5 \
    --comments-per-day 30 \
    --likes-per-day 100 \
    --applications-per-day 25
```

This lets you run a personal account more cautiously than a company page.

## Account isolation

LinkedIn cookies for one account are **never** used by another. The browser auto-picker uses the right cookies per account.

## Pausing / resuming

Pause an account when you don't want automated activity:

```bash
linkedin-mcp-install account pause company
# All scheduled posts for "company" are queued, not fired
linkedin-mcp-install account resume company
```

## Removing an account

```bash
linkedin-mcp-install account remove client-acme
# Prompts for confirmation
# Removes: cookies, drafts, schedules, profile
# Templates (shared) are kept
```

!!! warning
    Removing an account is irreversible. Back up first:

    ```bash
    tar czf client-acme-backup.tar.gz \
        ~/.linkedin-mcp/profile/accounts/client-acme/
    ```

## API

```python
accounts_list()
accounts_add(name="...", cookies=...)
accounts_activate(name="...")
accounts_remove(name="...")
accounts_pause(name="...")
accounts_resume(name="...")
```

See [REST API reference](../reference/api/index.md#accounts-multi-account).

---

## Next

- [Safety system](../operations/safety.md)
- [Dashboard tour](dashboard.md)