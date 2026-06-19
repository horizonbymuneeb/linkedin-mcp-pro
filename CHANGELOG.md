# Changelog

All notable changes to **linkedin-mcp-pro** are documented here.
This format follows [Keep a Changelog](https://keepachangelog.com/) and the project adheres to [Semantic Versioning](https://semver.org/).

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
