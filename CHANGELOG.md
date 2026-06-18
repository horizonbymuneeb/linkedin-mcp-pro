# Changelog

All notable changes to `linkedin-mcp-pro` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.1] — 2026-06-18

### Added
- **`scripts/cookie_to_profile.py`** — bootstrap a persistent browser session from a single li_at cookie. The QUICK path: paste cookie → 30 seconds later you have a profile that auto-refreshes forever.
  - Uses Playwright's `storage_state.json` API (more reliable than `launch_persistent_context` for HttpOnly cookies)
  - Captures 30+ LinkedIn cookies (li_at, JSESSIONID, bcookie, lidc, li_sugr, ...) in one go
  - Verifies login before saving (detects already-flagged cookies)
  - Supports `--cookie` inline, `--cookie-file` for non-standard path, `--profile-dir` for testing, `--force` to overwrite

### Fixed
- `use_profile_session.py` and `post_with_stealth.py` now correctly detect a profile in two layouts:
  - `state.json` (from `cookie_to_profile.py` — recommended)
  - `Default/Cookies` SQLite DB (from `bootstrap_session.sh` / `linkedin-mcp login`)
- `--check` flag in `use_profile_session.py` no longer accidentally posts
- Permission errors on root-only `/etc/linkedin-mcp-pro/li_at` no longer crash detection (uses `is_file()` with try/except)

## [0.4.0] — 2026-06-18

### Added
- **Profile sync workflow (Option A)** — copy your real laptop's Chrome profile to the server once; the server uses it as a Playwright persistent context. Cookie lifetime goes from days to 6-12 months. No more pasting fresh `li_at` cookies.
- **`scripts/bootstrap_session.sh`** — laptop-side: detects OS + Chrome profile, packages relevant files, transfers to server via direct scp, rsync over the cloudflared tunnel, or manual instructions.
- **`scripts/use_profile_session.py`** — EC2-side: posts to LinkedIn using the synced profile (no cookie file required). Reads `LINKEDIN_MCP_PROFILE_DIR` and `LINKEDIN_MCP_PROXY` env vars.
- **`scripts/sync_profile.sh`** — thin alias for `bootstrap_session.sh` (re-run to refresh after LinkedIn forces re-auth).
- **`scripts/termux_setup.sh`** — turn an Android phone into a SOCKS proxy host. Installs openssh (port 8022), cloudflared, sets up key-only auth, creates a `linkedin-proxy` helper command.
- **`scripts/post_with_stealth.py` (rewritten)** — auto-detects mode (profile vs cookie), supports `--profile-only` and `--cookie-only` flags, honors `LINKEDIN_MCP_PROXY` env var.
- **`docs/PROXY_SETUP.md`** — comprehensive guide for 5 proxy options (SOCKS via SSH, SOCKS via cloudflared, Termux phone, residential proxy services, WireGuard VPN), with diagrams, pros/cons, and step-by-step setup.
- **`docs/TERMUX_SETUP.md`** — full Termux phone guide including battery-saving tips, named-tunnel setup for stable URLs, and Android-specific troubleshooting.

### Changed
- `post_with_stealth.py` now uses Playwright's `launch_persistent_context` when a profile is available (matches the linkedin-mcp-pro `linkedin-mcp login` flow).
- All scripts honor `LINKEDIN_MCP_PROXY` (default: `socks5://127.0.0.1:1080`) so the same scripts work whether the proxy is SSH-tunneled, cloudflared, Termux, or a residential service.

### Migration from v0.3.0

Nothing required. The new scripts are additive:
- **Already using `linkedin-mcp login`?** Keep using it — the same `~/.linkedin-mcp/profile/` is now also usable by the standalone scripts.
- **Currently pasting cookies?** Switch to Option A: run `scripts/bootstrap_session.sh` on your laptop once.
- **No proxy yet?** Read `docs/PROXY_SETUP.md` and pick one.

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
