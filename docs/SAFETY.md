# linkedin-mcp-pro — Safety Design

> **Ban-prevention is the #1 design goal of this project.** Every feature
> decision is made in service of "don't get the user's account restricted."

## The threat model

LinkedIn actively detects and restricts accounts that:

1. **Send too many connection requests** (>100/week → temporary restriction)
2. **Use the same note verbatim** for many invitations → spam signal
3. **Post too frequently** (>1-2/day → distribution throttle)
4. **Send bulk messages** to non-connections → restriction
5. **Reply too fast** to many actions (no human delay) → bot signal
6. **Hit 429** repeatedly without backing off → IP/account flag
7. **Trigger CAPTCHA** and don't solve it → restriction
8. **Operate during off-hours** (3 AM activity) → bot signal

## Defenses (in order of execution)

### 1. Daily caps (hard, DB-enforced)

Every action type has a hard daily cap, configured in `SafetyConfig`:

```python
daily_limit_connection_requests: int = 20
daily_limit_posts: int = 2
daily_limit_messages: int = 30
daily_limit_comments: int = 30
daily_limit_reactions: int = 100
```

The cap is **enforced inside a SQLite transaction** — even if two MCP
clients connect simultaneously, the quota check is atomic.

### 2. Warm-up ramp (new accounts)

If your account is "fresh" (first 4 weeks), caps are lower:

| Week | Daily cap (connections) |
|---|---|
| 1 | 5 |
| 2 | 10 |
| 3 | 15 |
| 4+ | 20 (full) |

`account_age_weeks` is stored in `session_state` and updated manually:

```bash
sqlite3 data/linkedin-mcp-pro.db "INSERT OR REPLACE INTO session_state(key,value,updated_at) VALUES('account_age_weeks','2',datetime('now'));"
```

### 3. Business hours

By default, no writes outside 09:00-20:00 UTC, Mon-Fri. This:

- Mimics human activity patterns
- Avoids LinkedIn's "unusual hours" heuristics
- Lets the system back off naturally overnight

Configurable via env vars.

### 4. Jitter (3-15 min between actions)

Between actions, the system sleeps for a random duration in
`[ACTION_JITTER_MIN_SECONDS, ACTION_JITTER_MAX_SECONDS]`. This:

- Prevents burst patterns (LinkedIn's main bot signal)
- Spreads the day's actions across the business window
- Configurable: lower jitter = more actions per day, higher ban risk

### 5. Dry-run mode (preview before send)

Every write tool accepts `dry_run=true`. In dry-run mode:

- The action is **audited** (recorded as `status="dry_run"`)
- The SafetyGuard **raises `DryRun`** before any real work
- The MCP response shows exactly what *would* have been sent

This is **strongly recommended** for the first run after any config change.

### 6. 429 backoff (exponential, with consecutive-count)

When LinkedIn returns 429:

1. `record_429()` is called → increments `consecutive_429s` in DB
2. Backoff duration = `RATE_LIMIT_BACKOFF_BASE ** consecutive_429s` minutes
   - 1st 429 → 2 min
   - 2nd 429 → 4 min
   - 3rd 429 → 8 min
   - 4th 429 → 16 min
3. If `RATE_LIMIT_AUTO_PAUSE=true` (default), all writes are paused for 1 hour
4. A successful action resets the counter

### 7. CAPTCHA detection (hard pause 24h)

The browser client scans every page response for captcha patterns:

```python
CAPTCHA_PATTERNS = [
    "captcha",
    "challenge.{0,40}verification",
    "verify.{0,30}human",
    "unusual.{0,30}activity",
    "please.complete.a.security.check",
    "checkpoint.{0,20}required",
]
```

If matched:

1. `record_captcha(plan)` is called
2. `writes_paused_until` is set to **now + 24h**
3. All subsequent writes raise `RateLimitedError` for 24h
4. (Optional) Telegram alert if `ALERT_ON_CAPTCHA=true`

**We never auto-solve CAPTCHA.** That would be a ban-fast move.

### 8. Audit log (every action, forever)

Every action — dry-run, success, failure, blocked — is recorded:

```sql
SELECT created_at, action, status, target, detail
FROM audit_log
ORDER BY created_at DESC
LIMIT 50;
```

Use this to:

- Verify what was sent (and to whom)
- Diagnose quota exhaustion
- Investigate "why did I get restricted?"
- Show investors / compliance

Audit log is auto-pruned at 90 days (configurable via
`AUDIT_LOG_RETENTION_DAYS`).

## Action plan flow

```
                  ActionPlan
                      │
                      ▼
              ┌───────────────┐
              │  enforce()    │──── raises DryRun
              │               │──── raises OutsideBusinessHours
              │               │──── raises RateLimited (backoff)
              │               │──── raises QuotaExceeded
              └───────┬───────┘
                      │ all clear
                      ▼
              ┌───────────────┐
              │  browser.do() │──── detects captcha → record_captcha, raise
              │               │──── detects 429 → record_429, raise
              │               │──── network err → record_failure, raise
              └───────┬───────┘
                      │ success
                      ▼
              ┌───────────────┐
              │ record_success│──── increment quota
              │               │──── audit
              │               │──── reset 429
              └───────────────┘
```

## What we deliberately DON'T do

- **No proxy rotation.** Adds complexity, often detected.
- **No randomized fingerprints per session.** Stable fingerprint is more
  human-like than a new one every request.
- **No template-generated connection notes** (in v0.1). Same note = same fingerprint.
  Different notes per invite = better. Future: add a `note_templates` config.
- **No auto-CAPTCHA solving.** Banned in 24h if attempted.
- **No scraping user data at scale.** We only act on what the user explicitly
  asks for.

## Recommended daily workflow

```python
# Morning (10 AM): send 20 connection requests
#   (will be spread over 9:00-20:00 window with 3-15 min jitter)

# Noon: 1 post (max 1-2/day)

# Afternoon: respond to inbox messages (max 30/day)

# Review: check audit log, adjust limits
```

## What to do if your account gets restricted

1. **Stop all automation immediately.** `record_failure()` won't help.
2. **Run `linkedin-mcp-stats`** to see what you sent in the last 7 days.
3. **Lower your daily limits** in `.env` (e.g. 5 connections, 1 post).
4. **Increase jitter range** (e.g. 5-30 min).
5. **Wait 7-14 days** before resuming.
6. **Enable warm-up mode** (it's on by default for week 1-3).

## Why this is better than SaaS

- **You see the code.** Audit it, modify it, trust it.
- **You see the audit log.** SaaS doesn't show you everything.
- **You control the cap.** SaaS caps are tied to their pricing tier.
- **You can stop instantly.** SaaS has minimum contract periods.
