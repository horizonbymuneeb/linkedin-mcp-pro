# Changelog

All notable changes to `linkedin-mcp-pro` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
