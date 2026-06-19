# MCP tools reference

LinkedIn MCP Pro exposes **54 MCP tools** to AI agents via the Model Context Protocol.

The tools are grouped into families:

- [`drafts_*`](#drafts) — compose, edit, publish posts (6 tools)
- [`schedules_*`](#schedules) — cron + queue (5 tools)
- [`engagement_*`](#engagement) — comments, DMs, connections (8 tools)
- [`analytics_*`](#analytics) — read-only stats (5 tools)
- [`jobs_*`](#jobs) — CV, search, apply, tracker (12 tools)
- [`safety_*`](#safety) — gate inspection (3 tools)
- [`accounts_*`](#accounts) — multi-account (5 tools)
- [`templates_*`](#templates) — post templates (4 tools)
- [`webhooks_*`](#webhooks) — inbound integrations (3 tools)
- [`feed_*`](#feed-listener) — listening for posts (3 tools)

---

## drafts

### `drafts_list()`

List all saved drafts.

```json
[
  {"id": "draft_abc", "body": "...", "created_at": "..."}
]
```

### `drafts_get(id)`

Get a single draft.

### `drafts_create(body, media?, tags?)`

Create a new draft.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `body` | string | yes | Post text |
| `media` | string[] | no | Image URLs |
| `tags` | string[] | no | Hashtags |

Returns: `{id, ok, created_at}`

### `drafts_update(id, body?, tags?, media?)`

Update an existing draft. Only specified fields change.

### `drafts_delete(id)`

Permanently delete a draft.

### `drafts_publish(id, account?)`

Publish a draft. **Subject to safety gates.**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | yes | Draft ID |
| `account` | string | no | Account name (default: `default`) |

Returns: `{ok, post_id, posted_at}` or `{ok: false, reason: "..."}` if blocked.

---

## schedules

### `schedules_list()`

List pending schedules.

### `schedules_create(draft_id, at)`

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `draft_id` | string | yes | Draft ID |
| `at` | string | yes | ISO 8601 UTC timestamp |

### `schedules_update(id, at?)`

Reschedule.

### `schedules_cancel(id)`

Cancel a schedule (draft is preserved).

### `schedules_status()`

Get queue stats (pending, failed, posted today).

---

## engagement

### `engagement_list(kind, since?)`

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `kind` | string | yes | `mentions`, `comments`, `dms`, `connections`, `reactions` |
| `since` | string | no | ISO 8601 or duration (`24h`, `7d`) |

### `engagement_reply(id, body)`

Reply to a mention/comment.

### `engagement_hide(id, reason?)`

Hide a comment (e.g. spam).

### `engagement_accept_connection(id)`

Accept a pending connection request.

### `engagement_decline_connection(id, reason?)`

Decline.

### `engagement_send_connection(target, note?)`

Send a connection request.

### `engagement_like(post_id)`

Like a post.

### `engagement_follow(author_id)`

Follow an author.

---

## analytics

### `analytics_summary(days?)`

Overall KPIs for last N days (default 30).

### `analytics_posts(format?)`

List posts with engagement metrics. Format: `json` or `csv`.

### `analytics_best_times()`

Returns a heatmap of best posting times.

### `analytics_engagement_trend(days?)`

Daily engagement counts.

### `analytics_ab_tests()`

Active and completed A/B test results.

---

## jobs

### `jobs_cv_upload(file_path)`

Upload CV from local path.

### `jobs_cv_get()`

Get current CV metadata.

### `jobs_profile_get()`

Get profile (skills, target roles, etc).

### `jobs_profile_update(...)`

Update profile fields.

### `jobs_search(query, location?, filters?)`

Search jobs with match scoring.

### `jobs_apply(job_id, cover_letter_override?)`

Apply to a job.

### `jobs_tracker_list(status?)`

List applications.

### `jobs_tracker_get(application_id)`

Get single application.

### `jobs_tracker_delete(application_id)`

Delete (withdraws locally — LinkedIn side needs manual withdraw).

### `jobs_cover_letter_preview(job_id)`

Preview generated cover letter without applying.

### `jobs_settings_get()` / `jobs_settings_update(...)`

Module settings.

### `jobs_wizard_questions()` / `jobs_wizard_submit(answers)`

Interactive profile setup wizard.

---

## safety

### `safety_status()`

Current caps + usage today.

### `safety_test(action_type, account?)`

Test if a hypothetical action would be blocked.

### `safety_history(limit?)`

Recent gate decisions (block/allow).

---

## accounts

### `accounts_list()`

List all accounts.

### `accounts_add(name, cookies?)`

Add account (cookies optional — triggers browser login if omitted).

### `accounts_activate(name)`

Switch default account.

### `accounts_pause(name)` / `accounts_resume(name)`

Pause/resume automations for an account.

### `accounts_remove(name)`

Remove account.

---

## templates

### `templates_list()`

### `templates_create(name, body, variables?)`

### `templates_update(id, ...)`

### `templates_delete(id)`

---

## webhooks

### `webhooks_list()` / `webhooks_create(url, events?)` / `webhooks_delete(id)`

Inbound webhook subscriptions.

---

## feed-listener

### `feed_listen_start(topics?, accounts?)`

Start listening for posts matching topics on given accounts.

### `feed_listen_stop()`

Stop the listener.

### `feed_listen_status()`

Listener status.

---

## Calling tools from Claude Desktop

When you ask Claude "what tools do you have?", you'll see all 54 tools listed by name (e.g. `drafts_list`, `schedules_create`).

Example prompts:

- "Show me all my drafts"
- "Schedule draft `abc` for tomorrow at 9am"
- "Search for senior Python jobs in Remote"
- "Apply to job `123` with auto cover letter"

---

## Tool safety

Every write tool runs through the safety gate before executing. The gate:

1. Checks daily caps
2. Checks velocity windows
3. Checks content patterns
4. Returns a clear `reason` if blocked

Agents should always check the return value:

```python
result = drafts_publish(id="abc")
if not result["ok"]:
    print(f"Blocked: {result['reason']}")
else:
    print(f"Posted: {result['post_id']}")
```