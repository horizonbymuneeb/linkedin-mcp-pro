# Changelog

All notable changes to **linkedin-mcp-pro** are documented here.
This format follows [Keep a Changelog](https://keepachangelog.com/) and the project adheres to [Semantic Versioning](https://semver.org/).

## [2.2.0] - 2026-06-19

### Added
- **Jobs module** (`linkedin_mcp/jobs/`) — full LinkedIn auto-apply pipeline:
  - `cv_parser.py` — PDF/DOCX/TXT upload + extract skills/experience/education/email/phone/links
  - `profile.py` — 10-question wizard (work mode, cities, salary, role types, seniority, visa, blacklist, etc) with defaults seeded from CV
  - `matcher.py` — keyword overlap + skill-list bonus + (optional) sentence-transformers semantic similarity; 0-100 score with per-component reasons
  - `cover_letter.py` — LLM-generated (via existing pool) with 4 templates (default, concise, warm, founder) as fallback
  - `searcher.py` — LinkedIn Voyager client with stub fallback; respects easy-apply / remote / location filters
  - `applier.py` — eligibility gate (rate limit, blacklist, easy-apply, remote, score threshold) → match → cover letter → apply (dry-run by default) → tracker record
  - `tracker.py` — SQLite table for applications, status, match_score, cover_letter, notes
  - `jobs_router.py` — `/api/jobs/*` endpoints (health, cv/upload, wizard/questions, wizard/submit, profile GET/PUT, search, cover-letter/preview, apply, applications CRUD, settings, templates, reset)
- **jobs.html** UI — 4 tabs (Setup / Search / Apply / Tracker) with Linear-inspired design, drag-drop CV upload, score ring on each job card, cover letter editor with tone selector, applications table

### Changed
- Web UI: `web.py` mounts `jobs_router` and binds the project's DB to the jobs module (resolves the circular import via sys.modules lookup)
- Tests: 17 new tests in `tests/test_jobs.py` covering CV parsing, matcher, cover letter, profile, searcher, tracker (with sqlite-backed fixture), applier eligibility gates

### Total
- 721 tests passing (was 704)
- 41 web endpoints (was 31)
- 54 MCP tools (unchanged)

---

## [2.1.0] - 2026-06-19

### Added
- **10 missing API endpoints** wired up to fix all non-functional UI panels:
  - `GET /api/accounts`, `GET /api/accounts/` — list linked accounts
  - `GET /api/profile` — current profile (name, headline, summary, posts, connections)
  - `GET /api/audit` — last 100 audit events (filterable by status / action)
  - `GET /api/safety/status` — flat dict merged via `Object.assign` on frontend (kpis, hours, whitelist, blacklist)
  - `POST /api/safety/test` — evaluate a sample action against safety rules
  - `GET /api/engagement/`, `GET /api/engagement` — 30-day engagement stats
  - `POST /api/engagement/{kind}` — likes / comments / connects with dry-run support
  - `POST /api/cache/clear` — wipe session_state cache + `functools.lru_cache` in hot modules
  - `POST /api/server/restart` — schedule restart (systemd or in-process)
  - `POST /api/settings/reset` — scope-limited config reset (all / ui / llm / safety)
  - `POST /api/accounts/{id}/activate` — switch active account (profile panel)
- **Drafts composer rewrite** (Linear + LinkedIn feel) — 3 tabs (Compose / Templates / Recent), rich-text editor with B/I/U, lists, emoji, link, hashtag, mention toolbar, live LinkedIn-feed preview card, character counter ring (color-coded: blue/amber/red), per-save autosave to localStorage, save templates, search, delete with confirm, schedule post, toast notifications, words/lines/hashtags/mentions/read-time stats

### Changed
- Web UI: drafts page redesigned end-to-end (LinkedIn feed-style preview card with profile header, engagement bar, like/comment/repost/send row)
- Tests: 18 new tests for the 10 new endpoints (704 total, was 686)

### Total
- 704 tests passing
- 31 web endpoints
- 54 MCP tools

---

## [2.0.0] - 2026-06-19

### Added
- **11-panel Tailwind+Alpine Web UI** with dark mode, responsive grids, gradient hero cards, smooth transitions
- **Interactive install wizard** (`linkedin-mcp-install`): doctor, detect, add, remove, print-configs, verify
- **LLM API key management** — 6 providers (OpenAI, Anthropic, OpenRouter, NVIDIA, Custom, Pool), with masked key display, test connection, base_url override
- **Ban-safety gate** — daily limits, business hours enforcement, whitelist/blacklist, account age minimums
- **Auto-engagement** — auto_like, auto_comment, auto_connect with dry-run + warmup
- **Audit log** — 50-row scrollable table with action/status/date filters, live indicator
- **Real-time SSE** for live log streaming

### Changed
- Web UI: migrated from pico.css to Tailwind CSS + Alpine.js (CDN)
- All panels: sticky top nav, dark/light/system theme, mobile responsive, Inter font
- CLI: split into `linkedin-mcp` (server) and `linkedin-mcp-install` (wizard)

### Total
- 685 tests passing (581 base + 42 install + 62 LLM keys)
- 54 MCP tools
- 21 web endpoints
- 9 CLI commands

---

## [1.1.0] - 2026-05-28

### Added
- 10 new MCP tools: auto_like, auto_comment, auto_connect, add_llm_key, list_llm_keys, test_llm_key, remove_llm_key, get_safety_config, set_safety_config, check_action
- Web UI v1 (pico.css)
- 581 base tests

## [1.0.0] - 2026-04-12

### Added
- Initial public release
- 44 MCP tools (profile, posts, search, engagement, analytics, schedules, templates, drafts)
- Cookie-based authentication via Playwright
- SOCKS5 / HTTP proxy support
- Basic CLI: `linkedin-mcp`

## [0.9.0] - 2026-03-01

### Added
- Pre-release: prototype with 12 core tools

## [0.5.0] - 2026-02-08

### Added
- Internal alpha: 4 tools, basic Playwright login flow
