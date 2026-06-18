# linkedin-mcp-pro

Open-source MCP server for LinkedIn — profiles, search, jobs, posts, connections, messages. Self-hosted, ban-safe, MIT licensed.

> ⚠️ **Disclaimer:** This is an independent, community project. It is not affiliated with, authorized by, endorsed by, or sponsored by LinkedIn Corporation or Microsoft. "LinkedIn" is a registered trademark of LinkedIn Corporation. Use of this tool must comply with LinkedIn's [Terms of Service](https://www.linkedin.com/legal/user-agreement) — automation may carry account-restriction risk.

## What is it?

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that exposes your LinkedIn account as 23 tools for any MCP client — with **ban-prevention built in** (daily caps, jitter, business hours, captcha detection).

**22 tools** | **Python 3.11+** | **MIT License**

## Quick links

- 📖 [README.md](README.md) — install, usage, examples
- 🏗️ [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — request flow, modules
- 🛡️ [docs/SAFETY.md](docs/SAFETY.md) — ban-prevention design
- 🤝 [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) — dev guide
- 📋 [CHANGELOG.md](CHANGELOG.md) — release notes

## Install (1 minute)

```bash
git clone https://github.com/your-org/linkedin-mcp-pro
cd linkedin-mcp-pro
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env
# Edit .env with your LI_AT cookie (see README)
.venv/bin/linkedin-mcp-health
```

## License

MIT — see [LICENSE](LICENSE).

## Legal

This project automates a third-party service. By using it, you agree to LinkedIn's Terms of Service. The authors disclaim all liability for account actions. Use responsibly.
