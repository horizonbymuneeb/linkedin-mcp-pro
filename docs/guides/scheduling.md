# Scheduling

The scheduler is a cron-like queue that fires posts at specific times, subject to safety gates.

![Schedule view](../assets/images/schedules.png)

## Concepts

| Term | Meaning |
|------|---------|
| **Schedule** | A draft + fire time + target account |
| **Queue** | All pending schedules, ordered by fire time |
| **Worker** | Background process that polls the queue every 30s |
| **Gate** | Pre-publish safety check |

## Creating a schedule

### From drafts page

Open a draft → click **Schedule for** → pick date/time → **Save**.

### From schedules page

Click **New schedule** → pick a draft from dropdown → pick time → **Save**.

### CLI

```bash
linkedin-mcp-schedule add \
    --draft-id 123 \
    --at "2026-01-15T14:00:00Z" \
    --account default
```

### MCP tool

```python
schedules_create(draft_id="123", at="2026-01-15T14:00:00Z")
```

---

## Queue view

The `/schedules` page shows the next 7 days as a calendar:

```
       Mon   Tue   Wed   Thu   Fri   Sat   Sun
 09:00 [✓]   [ ]   [✓]   [ ]   [ ]   [ ]   [ ]
 12:00 [ ]   [✓]   [ ]   [✓]   [ ]   [ ]   [ ]
 15:00 [✓]   [ ]   [ ]   [ ]   [✓]   [ ]   [ ]
 18:00 [ ]   [ ]   [✓]   [ ]   [ ]   [ ]   [ ]
```

Hover any dot → preview the post. Click → edit/cancel.

---

## Worker

The worker is started automatically when `linkedin-mcp-web` boots. Manual start:

```bash
linkedin-mcp-schedule worker
```

The worker:

1. Polls the queue every 30 seconds (configurable via `LINKEDIN_MCP_SCHEDULER_INTERVAL`)
2. Picks up due schedules
3. Runs the **safety gate** (see [safety.md](../operations/safety.md))
4. If gate passes: publishes via Voyager + marks `posted`
5. If gate fails: marks `gate_failed` with reason, posts to audit log

---

## Recurring schedules

Want to post every Tuesday at 9am?

```bash
linkedin-mcp-schedule add \
    --draft-id 123 \
    --cron "0 9 * * 2" \
    --account default
```

Uses standard cron syntax. View/manage recurring: `/schedules?view=recurring`.

---

## Multi-account scheduling

Each account has its own queue. The scheduler dispatches to the right account automatically.

```bash
# Queue for account "personal"
linkedin-mcp-schedule add --draft-id 123 --at "..." --account personal

# Queue for account "company"
linkedin-mcp-schedule add --draft-id 456 --at "..." --account company
```

---

## Time zones

All timestamps are stored as UTC. The UI converts to your browser's timezone for display. You can override per-account in `/settings`.

---

## Failure modes

| Symptom | Cause | Fix |
|---------|-------|-----|
| Schedule stuck in `pending` | Worker not running | `linkedin-mcp-schedule worker` |
| Marked `gate_failed` | Safety gate tripped | Check `/audit`, adjust caps |
| Marked `error` | LinkedIn rejected | Check `/audit`, may need to retry manually |
| Posted but missing on LinkedIn | API delay | Wait 60s, refresh LinkedIn |

---

## Deadman switch

If scheduled posts stop firing for >24h (e.g. server died), the deadman watchdog pings you. Configure at `/settings` → Notifications.

---

## Next

- [Engagement](engagement.md)
- [Safety system](../operations/safety.md)