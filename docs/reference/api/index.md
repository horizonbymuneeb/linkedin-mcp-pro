# REST API reference

The web server exposes **60+ REST endpoints** organized into routers.

Base URL: `http://localhost:8080`

All endpoints return JSON unless noted. All `POST`/`PUT` accept JSON.

---

## System

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/version` | App version + Python version |
| `GET` | `/api/summary` | Dashboard summary (KPIs, counts) |
| `POST` | `/api/cache/clear` | Clear in-memory caches |
| `POST` | `/api/server/restart` | Restart uvicorn worker |
| `POST` | `/api/settings/reset` | Reset all settings to defaults |

### `GET /api/version`

```json
{
  "version": "2.3.1",
  "python": "3.13.5",
  "platform": "linux"
}
```

---

## Drafts

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/drafts` | List all drafts |
| `POST` | `/api/drafts` | Create draft |
| `POST` | `/api/drafts/save` | Save/update draft |
| `DELETE` | `/api/drafts/{draft_id}` | Delete draft |
| `POST` | `/api/post` | Publish a post (subject to safety gates) |

### `POST /api/drafts/save`

Request:

```json
{
  "id": "draft_abc123",
  "body": "Excited to announce...",
  "media": [],
  "tags": ["ai", "agents"]
}
```

Response:

```json
{
  "ok": true,
  "id": "draft_abc123",
  "saved_at": "2026-01-15T14:00:00Z"
}
```

---

## Schedules

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/schedules` | List pending schedules |

---

## Templates

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/templates` | List post templates |

---

## Engagement

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/engagement` | Aggregated engagement counts |
| `GET` | `/api/engagement/` | List engagement items (with `?kind=mentions`) |
| `POST` | `/api/engagement/{kind}` | Action on engagement item (reply, hide, etc.) |

`kind` is one of: `mentions`, `comments`, `dms`, `connections`, `reactions`.

---

## Profile

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/profile` | Synced LinkedIn profile |
| `GET` | `/api/profile/health` | Profile sync status |

---

## Cookies & login

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/cookies` | Current cookies metadata (no values) |
| `GET` | `/api/cookies/health` | Cookie freshness + LinkedIn session check |
| `POST` | `/api/cookies/li_at` | Save `li_at` cookie value |
| `POST` | `/api/cookies/import` | Import cookies from JSON file |
| `GET` | `/api/login/status` | Background login job status |
| `POST` | `/api/login/start` | Start browser-driven login |
| `POST` | `/api/login/cancel` | Cancel running login |

### `POST /api/cookies/li_at`

Request:

```json
{ "li_at": "AQEDASn...", "account": "default" }
```

Response:

```json
{ "ok": true, "expires_at": "2026-04-15T14:00:00Z" }
```

---

## Accounts (multi-account)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/accounts` | List accounts |
| `GET` | `/api/accounts/` | Same, with trailing slash |
| `POST` | `/api/accounts/{account_id}/activate` | Switch default account |

---

## Safety

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/safety/status` | Current caps + usage today |
| `POST` | `/api/safety/test` | Test gate against a hypothetical action |

---

## Audit

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/audit` | Audit log entries (`?limit=50&offset=0`) |

---

## Deadman

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/deadman` | Watchdog status |

---

## Jobs module

All routes mounted under `/api/jobs/` (or relative paths listed).

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/jobs/health` | Module health |
| `POST` | `/api/jobs/cv/upload` | Upload CV (multipart/form-data) |
| `GET` | `/api/jobs/cv` | Current CV metadata |
| `GET` | `/api/jobs/wizard/questions` | Wizard questions |
| `POST` | `/api/jobs/wizard/submit` | Submit wizard answers |
| `GET` | `/api/jobs/profile` | Get profile |
| `PUT` | `/api/jobs/profile` | Update profile |
| `POST` | `/api/jobs/search` | Search jobs |
| `POST` | `/api/jobs/cover-letter/preview` | Preview cover letter |
| `POST` | `/api/jobs/apply` | Submit application |
| `GET` | `/api/jobs/applications` | Tracker entries |
| `DELETE` | `/api/jobs/applications/{application_id}` | Delete tracker entry |
| `GET` | `/api/jobs/settings` | Module settings |
| `PUT` | `/api/jobs/settings` | Update module settings |
| `GET` | `/api/jobs/templates` | Cover letter templates |
| `POST` | `/api/jobs/reset` | Reset module |

### `POST /api/jobs/cv/upload`

```bash
curl -X POST http://localhost:8080/api/jobs/cv/upload \
    -F "file=@resume.pdf"
```

Response:

```json
{
  "ok": true,
  "cv_id": "cv_abc123",
  "version": 3,
  "parsed": {
    "contact": {"email": "...", "phone": "...", "location": "..."},
    "experience": [...],
    "skills": ["python", "fastapi", "..."]
  }
}
```

### `POST /api/jobs/search`

Request:

```json
{
  "query": "Senior Python Engineer",
  "location": "Remote",
  "filters": {
    "date_posted": "week",
    "experience_level": ["mid", "senior"],
    "remote": true
  }
}
```

Response:

```json
{
  "results": [
    {
      "id": "job_123",
      "title": "Senior Python Engineer",
      "company": "Acme Corp",
      "location": "Remote",
      "match_score": 87,
      "match_label": "Strong match",
      "url": "https://linkedin.com/jobs/view/123"
    }
  ],
  "total": 42
}
```

### `POST /api/jobs/apply`

Request:

```json
{
  "job_id": "job_123",
  "cover_letter_override": "..."   // optional
}
```

Response:

```json
{
  "ok": true,
  "application_id": "app_456",
  "status": "submitted",
  "applied_at": "2026-01-15T14:00:00Z"
}
```

---

## LLM keys

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/llm/providers` | List configured providers |
| `GET` | `/api/llm/providers/{name}` | Get provider config |
| `POST` | `/api/llm/providers` | Add provider |
| `DELETE` | `/api/llm/providers/{name}` | Remove provider |
| `POST` | `/api/llm/providers/{name}/test` | Test provider connection |
| `POST` | `/api/llm/test-all` | Test all providers |
| `GET` | `/api/llm/default` | Get default provider |
| `POST` | `/api/llm/default` | Set default provider |
| `GET` | `/api/llm/models/{name}` | List models for a provider |

---

## Installer

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/doctor` | System readiness check |
| `GET` | `/agents` | List supported MCP hosts |
| `GET` | `/agents/{agent}/config` | Get config snippet for an agent |
| `POST` | `/install/{agent}` | Wire an agent |
| `DELETE` | `/uninstall/{agent}` | Unwire an agent |
| `GET` | `/verify/{agent}` | Verify wiring |

---

## Error format

All errors return:

```json
{
  "detail": "Human-readable message"
}
```

Status codes:

| Code | Meaning |
|------|---------|
| 200 | OK |
| 201 | Created |
| 400 | Bad request (invalid body) |
| 401 | Unauthenticated |
| 403 | Forbidden (safety gate blocked) |
| 404 | Not found |
| 409 | Conflict (duplicate) |
| 422 | Validation error |
| 429 | Rate limited |
| 500 | Server error |
| 503 | Service unavailable |

---

## Authentication

The API is **unauthenticated by default** (assumes local use).

To enable token auth:

```bash
export LINKEDIN_MCP_API_TOKEN="your-secret-token"
linkedin-mcp-web
```

Then all requests must include:

```bash
-H "Authorization: Bearer your-secret-token"
```

For multi-user deployments, use a reverse proxy (nginx, Caddy) for HTTPS + auth.