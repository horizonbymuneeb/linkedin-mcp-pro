# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 2.0.x   | ✅ Active development |
| 1.1.x   | ✅ Security fixes    |
| 1.0.x   | ⚠️ Critical fixes only |
| < 1.0   | ❌ End of life      |

## Reporting a Vulnerability

Email: **horizonbymuneeb@gmail.com**
(or open a private security advisory on GitHub)

**Please do not** open a public issue for security vulnerabilities.

Response SLA: **48 hours** acknowledgment, **7 days** for critical fix.

## Cookie & Credential Safety

- `li_at` cookies stored in `~/.linkedin-mcp/` with mode **0700**.
- Never commit `storage_state.json` or any `*.cookie` files.
- `.gitignore` already excludes these patterns.
- LLM API keys stored in `/etc/linkedin-mcp-pro/keys.json` (**600**, root-only) when using sudo install, otherwise `~/.config/linkedin-mcp-pro/keys.json` (**600**, user-only).

## LinkedIn Account Safety

- **Voyager API is BANNED** for posting — we use only web scraping via Playwright.
- Daily limits enforced: likes, comments, connects, posts.
- Business hours enforcement (default: 9 AM – 6 PM in your TZ).
- Account age minimum: **30 days**.
- Warmup period: **14 days × 0.2x multiplier** for new accounts.
- `dry_run=true` is the default — opt-in to live mode.

## Threat Model (out of scope)

- Compromise of your local machine / browser profile.
- Disclosure of your `li_at` cookie via means outside this project (e.g., accidental commit, screenshot).
- LinkedIn changing their HTML in ways that break scraping — we ship best-effort selectors.

## Dependencies

We run `pip-audit` and `safety` on every release. CVEs in transitive deps are patched within 7 days for critical, 30 days for high.
