# Roadmap

**High-level direction only.** Detailed planning, internal tactics, and ban-safety strategies are kept private to protect against evasion.

## ✅ Shipped

### v1.0.0 — Tier 3 + Tier 4 (engagement + analytics)

- 54 MCP tools: reads, writes, templates, scheduling, analytics, multi-account, A/B testing, RSS, shadow-ban detection
- FastAPI web dashboard on port 8080
- Multi-platform scaffold (Twitter/X, Threads, Bluesky)
- AI engagement coach, content calendar, lead scraper, competitor monitor

### v1.1.0 — Cookies + Login + Tier 3 ban-safety

- Web UI cookies panel (`/static/cookies.html`)
- Three login methods: browser automation, paste `li_at`, import full cookie JSON
- Live LinkedIn session health check
- CLI flags for headless/headed login
- Ban-safety infrastructure (gates all auto-engagement)

### v2.0.0 — Universal install + LLM keys

- `linkedin-mcp-install` CLI: doctor / detect / add / remove / verify
- One-line installers: `curl | bash` (Linux/macOS) and `iwr | iex` (Windows)
- 10+ supported agents: Claude Desktop, Claude Code, Cursor, Cline, Continue.dev, Windsurf, Zed, Open WebUI, LibreChat, LobeChat
- LLM API key management for 6 providers (OpenAI, Anthropic, OpenRouter, Ollama, local pool, custom)
- 4 new MCP tools: `llm_list_providers`, `llm_add_key`, `llm_remove_key`, `llm_test_key`
- Polished Web UI: 12 panels with Tailwind + Alpine.js, dark mode, mobile-responsive
- 685+ tests passing

## 🚧 In progress

- Tier 4 polish: charts, real-time audit stream, multi-account switcher
- Documentation refresh (AGENTS.md, INSTALL.md, SECURITY.md)

## 💭 Considering

- Tauri native desktop app (`.dmg` / `.exe` / `.AppImage`)
- Mobile companion app (iOS/Android wrapper around web UI)
- Voice-to-post with Whisper (already in v1.1.0, needs packaging)
- Carousel generator (PDF → multi-page upload)

## 🔒 Safety note

This tool helps users **stay safe** on LinkedIn by enforcing business-hours windows, daily quotas, blacklist/whitelist keywords, and warmup periods. We do **not** support mass spam, scraper resale, or any use that violates LinkedIn's Terms of Service.

## 📊 Stats

| Surface | Count |
|---|---|
| MCP tools | 58+ |
| CLI commands | 10+ |
| Web UI panels | 12 |
| Tests | 685+ passing |
| Supported agents | 10+ |
| LLM providers | 6 |