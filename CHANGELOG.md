# Changelog

All notable changes to `linkedin-mcp-pro` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] — 2026-06-18

### Added
- **`linkedin-mcp login` CLI command** — opens a Chromium browser, you log in normally (email/password/2FA), the session is captured and reused for all future calls. Replaces the manual `li_at` cookie extraction flow.
- **Persistent browser profile at `~/.linkedin-mcp/profile/`** — standard Chromium user-data-dir, created on first login. Survives reinstalls, portable across machines, can be opened with `chromium --user-data-dir=~/.linkedin-mcp/profile` for debugging.
- **`BrowserChallenge` exception** — raised when LinkedIn shows a captcha / security check / 2FA interstitial. Browser window stays open so the user can solve the challenge in-place, then retry the failed command.
- **`LINKEDIN_MCP_PROFILE_DIR` env var** — override the default profile location (`~/.linkedin-mcp/profile/`). Useful for Docker volume mounts or shared profiles across users.
- **`LI_AT` env var is now optional** — used as a fallback only when no browser profile exists at `~/.linkedin-mcp/profile/`. Browser session is the primary auth path.
- **52 new tests** in `test_v0_3_features.py` (179/179 total now passing)

### Changed
- **Default browser profile path:** `./data/browser-profile/` → `~/.linkedin-mcp/profile/` (now outside the project dir, survives reinstalls and `git clean`).
- **`BrowserClient` now requires a profile by default** — no longer accepts a bare `li_at` cookie as the sole auth input. Use `linkedin-mcp login` to bootstrap the profile, or set `LI_AT` for the fallback path.
- **`_check_for_challenges()` enhanced** — now detects captcha, email-verification, "unusual activity" interstitials, and redirect-to-login; raises `BrowserChallenge` with the live page URL so the user can solve it manually.
- **Cookie lifetime** — effectively unlimited for browser-session users (the browser refreshes `li_at` automatically, typically 6-12 months). `LI_AT`-only users still see ~7-day rotation.

### Migration from v0.2.0

1. Upgrade: `pip install -U linkedin-mcp-pro`
2. Run once: `linkedin-mcp login` — log in normally, profile is saved to `~/.linkedin-mcp/profile/`.
3. Optionally remove `LI_AT` from your `.env` (or leave it as a fallback — it'll be ignored while a profile exists).
4. Restart the MCP server.

Headless users (no display) cannot run `linkedin-mcp login` interactively. For those:
- **Option A:** Create the profile on a local machine, then `scp -r ~/.linkedin-mcp/profile user@server:~/.linkedin-mcp/profile` to the server.
- **Option B:** Keep using `LI_AT` env var as before — the fallback path is preserved.

## [0.2.0] — 2026-06-18

### Added
- **Comment on posts** with full URL or URN support (previously stubbed)
- **React to posts** with 8 reaction types: LIKE, CELEBRATE, INSIGHTFUL, LOVE, SUPPORT, FUNNY, CURIOUS, MIND
- **Media upload in posts** — image (.jpg/.png/.gif) and video (.mp4/.mov) up to 200MB
- **Delete post** — actual implementation (navigate, overflow menu, confirm)
- **Note template rotation** — `connect.pick_note()` for varied connection request notes (anti-fingerprint)
- **`BrowserClient.upload()`** — new method for file uploads
- **`_validate_urn_or_url()`** helper — accepts both URLs and URNs across engage/post/connect modules
- **42 new tests** in `test_v0_2_features.py` (127/127 total now passing)
- **USAGE.md** — comprehensive user-facing guide with workflow examples

### Changed
- `comment_on_post` and `react_to_post` now accept either `post_url` or `post_urn` (renamed param: `target`)
- `delete_post` now accepts URL or URN (renamed param: `target`)
- `create_post` uses `media_path` (local file) instead of `media_url` (was a placeholder)
- Switched from Patchright to **agent-browser** (Vercel Labs, 36k★) — 1065 fewer lines of code
- Removed AI/Claude mentions from user-facing strings and code comments

### Fixed
- Browser module reduced from 1,397 → 813 lines (subprocess wrapper, simpler)
- Real Chrome (Chrome for Testing) instead of Chromium for better stealth

## [0.1.0] — 2026-06-18

### Added
- Initial release
- 12 read tools (Voyager HTTP API):
  - `get_my_profile`, `get_person_profile`
  - `search_people`, `search_jobs`, `search_companies`
  - `get_job_details`, `get_company_profile`, `get_company_employees`
  - `get_feed`, `get_inbox`, `get_conversation`, `get_pending_invitations`
- 10 write tools (Patchright browser automation):
  - `send_connection_request` (with optional note)
  - `create_post` (text + optional media URL)
  - `delete_post`
  - `comment_on_post`
  - `react_to_post` (LIKE, CELEBRATE, INSIGHTFUL, LOVE, SUPPORT, FUNNY)
  - `send_message`
  - `accept_invitation`, `decline_invitation`, `withdraw_invitation`
- 2 stats tools:
  - `get_daily_stats` (quota usage per action)
  - `get_audit_log` (last N actions with status and detail)
- Safety layer:
  - Daily caps, DB-enforced
  - Warm-up ramp (weeks 1-3 lower than week 4+)
  - Business hours enforcement
  - Jitter (3-15 min between actions, configurable)
  - 429 backoff with consecutive-count
  - CAPTCHA detection (24h hard pause)
  - Dry-run mode on every write tool
  - Full audit log (auto-pruned at 90 days)
- SQLite-backed state (quotas, queue, audit, session)
- Configuration via env vars (with `.env` file support)
- File-based secrets (recommended for production: `LI_AT_FILE`, `JSESSIONID_FILE`)
- CLI tools:
  - `linkedin-mcp-health` — verify config, DB, daily usage
  - `linkedin-mcp-stats` — print audit log
- Multiple deployment options:
  - pip install (`pip install -e .`)
  - Docker + docker-compose
  - systemd service
- Comprehensive tests (73 unit tests, all passing)
- Documentation:
  - README with install, usage, examples
  - ARCHITECTURE.md (request flow, module layout)
  - SAFETY.md (ban-prevention deep dive)
  - CONTRIBUTING.md (dev guide)

### Security
- Cookie stored in `/etc/linkedin-mcp-pro/li_at` (600 perms, root-only)
- `li_at` never logged, never committed (gitignored)
- All API calls use realistic browser headers to avoid obvious bot detection

### Known limitations
- Voyager API endpoints are undocumented; some may need URL tweaks as LinkedIn changes
- Browser selectors may break when LinkedIn updates UI (mark as TODO when fixed)
- Easy Apply for jobs is NOT supported (would require form automation; out of scope)
- This is v0.1 — APIs may shift before v1.0
