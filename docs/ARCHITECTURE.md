# linkedin-mcp-pro — Architecture

This document describes the runtime architecture: how the 22 MCP tools flow
through reads, writes, safety, and storage.

## High-level diagram

```
                        ┌─────────────────────────┐
   Claude / Cursor      │  MCP client             │
   (any MCP-compatible) │  (stdio / stream-http)  │
                        └──────────┬──────────────┘
                                   │ JSON-RPC
                                   ▼
                        ┌─────────────────────────┐
                        │  linkedin-mcp-pro       │
                        │  server.py (22 tools)   │
                        └──────────┬──────────────┘
                                   │
        ┌──────────────────────────┼────────────────────────────┐
        │                          │                            │
        ▼                          ▼                            ▼
┌──────────────┐         ┌──────────────────┐         ┌──────────────────┐
│ Voyager API  │         │  Patchright      │         │ SafetyGuard      │
│ (HTTP reads) │         │  (browser writes)│         │ (quota, hours,   │
│              │         │                  │         │  warmup, 429)    │
│ api/*.py     │         │ browser/*.py     │         │ safety.py        │
└──────┬───────┘         └────────┬─────────┘         └────────┬─────────┘
       │                          │                            │
       └─────────────┬────────────┴────────────────────────────┘
                     ▼
              ┌────────────────┐
              │ SQLite         │
              │ (data/*.db)    │
              │  - daily_quotas│
              │  - action_queue│
              │  - audit_log   │
              │  - session     │
              └────────────────┘
```

## Module layout

```
linkedin_mcp/
├── __init__.py        # version, license
├── config.py          # env loading, validation, dataclasses
├── db.py              # SQLite wrapper (quotas, queue, audit, state)
├── safety.py          # SafetyGuard (the ban-prevention layer)
├── server.py          # MCP server, 22 tools, dispatchers, lifespan
├── cli.py             # linkedin-mcp-health, linkedin-mcp-stats
├── api/               # READ layer
│   ├── __init__.py
│   ├── client.py      # VoyagerClient (httpx async, stealth headers)
│   ├── profile.py     # get_my_profile, get_person_profile
│   ├── search.py      # search_people, search_jobs, search_companies
│   ├── feed.py        # get_feed
│   ├── messaging.py   # get_inbox, get_conversation, get_pending_invitations
│   └── jobs.py        # get_job_details, get_company_employees
└── browser/           # WRITE layer
    ├── __init__.py
    ├── client.py      # BrowserClient (Patchright context manager)
    ├── auth.py        # session load / cookie bootstrap
    ├── connect.py     # send_connection_request, accept/decline/withdraw
    ├── post.py        # create_post, delete_post
    ├── engage.py      # comment_on_post, react_to_post
    └── message.py     # send_message
```

## Request lifecycle (a write tool example)

`send_connection_request(public_id="alice", note="hi")`:

1. **MCP client** (Claude) sends JSON-RPC to `server.py`
2. **`server.call_tool()`** routes to `_dispatch_write`
3. **Build `ActionPlan`** with action="connection", target="linkedin.com/in/alice"
4. **`guard.enforce(plan)`** runs three checks:
   - Business hours (current UTC time in 9-20? weekday in allowed set?)
   - Hard pause (captcha or 429 cooldown active?)
   - Daily quota (used < limit, factoring in warm-up ramp?)
5. **If `dry_run=true`**: raise `DryRun` → audit recorded, return preview
6. **If real**: call `browser.send_connection_request(public_id, note)`
   - `BrowserClient` navigates to profile page
   - Clicks Connect button
   - Fills note dialog (if note provided)
   - Submits
7. **Detect captcha/429** in response page:
   - If captcha: call `guard.record_captcha(plan)`, raise `CaptchaDetectedError`
   - If 429: call `guard.record_429()`, raise `RateLimitedError`
8. **`guard.record_success(plan, result)`**:
   - Increment `daily_quotas` row
   - Append `audit_log` entry with status="success"
   - Reset any 429 backoff if this succeeded
9. **Return** result dict to MCP client as `TextContent`

## Read tool lifecycle

`search_jobs(keywords="AI engineer", location="SF")`:

1. **MCP client** sends tool call
2. **`server.call_tool()`** routes to `_dispatch_read`
3. **`_with_voyager(api.search_jobs, ...)`** opens fresh `VoyagerClient`:
   - Builds stealth headers (UA, Accept, x-restli-protocol-version, csrf-token)
   - Async context: `httpx.AsyncClient` opens connection
4. **`api.search_jobs(client, ...)`** calls `client.get("/jobs/search", params=...)`
5. **Errors**:
   - 401/403 → `AuthError` (caller should re-login)
   - 429 → `RateLimitError` (with `retry_after_seconds` from `Retry-After` header)
   - 5xx → tenacity retries 3x, exponential backoff
6. **Return** raw dict → JSON → `TextContent`

No safety check on reads (they don't consume quota, no ban risk).

## Database schema

See `db.py`. Five tables:

```sql
-- daily_quotas: rolling daily counter per action type
CREATE TABLE daily_quotas (
    day TEXT, action TEXT, count INT, last_action_at TEXT,
    PRIMARY KEY (day, action)
);

-- action_queue: pending writes (jittered, scheduled)
CREATE TABLE action_queue (
    id INTEGER PRIMARY KEY,
    action TEXT, payload TEXT (JSON), scheduled_at TEXT,
    status TEXT,  -- pending|executing|done|failed|cancelled
    created_at, started_at, completed_at TEXT,
    error TEXT, result TEXT (JSON)
);

-- audit_log: every action ever taken
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY,
    action TEXT, target TEXT, status TEXT,
    dry_run INT, detail TEXT (JSON), created_at TEXT
);

-- session_state: warmup, 429 backoff, captcha pause
CREATE TABLE session_state (
    key TEXT PRIMARY KEY, value TEXT, updated_at TEXT
);
```

## Safety design (the core of the project)

See `docs/SAFETY.md` for the full design. Quick summary:

| Concern | Defense | Implementation |
|---|---|---|
| Quota | DB-enforced hard cap, warm-up ramp | `SafetyGuard.enforce()` |
| Time | Only run in business hours | `_is_in_business_hours()` |
| 429 | Exponential backoff, consecutive-count | `record_429()` |
| Captcha | Hard pause 24h, never auto-resolve | `record_captcha()` |
| Audit | Every action recorded | `db.audit()` |
| Jitter | 3-15 min random delay | `jitter_seconds()` |
| Dry-run | Preview without sending | `ActionPlan(dry_run=True)` |

## Threading model

- **Single-threaded async** (asyncio). SQLite is guarded by a thread lock.
- **httpx.AsyncClient** per request (cheap, no pool needed).
- **Patchright browser** is shared across the BrowserClient lifetime (one
  Chromium instance, multiple pages as needed).
- **MCP transport** is stdio by default (one process per MCP client). For
  multiple clients, run multiple instances or use HTTP transport.

## Failure modes

| Failure | Behavior |
|---|---|
| `li_at` cookie expired | `AuthError` raised, MCP returns text error |
| LinkedIn returns 429 | `RateLimitError` with `retry_after_seconds` |
| Captcha detected | `CaptchaDetectedError`, writes paused 24h |
| Network blip | httpx retries (5xx), tenacity handles backoff |
| Quota exhausted | `QuotaExceededError`, retry tomorrow |
| Outside hours | `OutsideBusinessHoursError`, retry at next open window |
| Browser crash | Exception propagated, audit records `failed` |
