# MCP Agent Compatibility

LinkedIn MCP Pro works as a stdio MCP server, so it plugs into any host that
launches MCP servers via a `command` + `args` pair in a JSON config. The
installer ships with first-class support for the following agents.

| Agent             | OS            | Config path                                                    | Install command                                  |
|-------------------|---------------|----------------------------------------------------------------|--------------------------------------------------|
| Claude Desktop    | macOS         | `~/Library/Application Support/Claude/claude_desktop_config.json` | `linkedin-mcp-install add claude-desktop-mac`    |
| Claude Desktop    | Linux         | `~/.config/Claude/claude_desktop_config.json`                  | `linkedin-mcp-install add claude-desktop-linux`  |
| Claude Desktop    | Windows       | `%APPDATA%/Claude/claude_desktop_config.json`                  | `linkedin-mcp-install add claude-desktop-win`    |
| Claude Code       | All           | `~/.claude.json`                                               | `linkedin-mcp-install add claude-code`           |
| Cursor            | All           | `~/.cursor/mcp.json`                                           | `linkedin-mcp-install add cursor`                |
| Cline (VS Code)   | All           | `~/.cline/mcp_settings.json`                                   | `linkedin-mcp-install add cline`                 |
| Continue.dev      | All           | `~/.continue/config.json`                                      | `linkedin-mcp-install add continue`              |
| Windsurf          | All           | `~/.codeium/windsurf/mcp_config.json`                          | `linkedin-mcp-install add windsurf`              |
| Zed               | All           | `~/.config/zed/settings.json`                                  | `linkedin-mcp-install add zed`                   |
| Open WebUI        | All           | `~/.open-webui/mcp_servers.json`                               | `linkedin-mcp-install add open-webui`            |
| LibreChat         | All           | `~/.librechat/librechat.yaml`                                  | `linkedin-mcp-install add librechat`             |
| LobeChat          | All           | `~/.lobe-chat/mcp.json`                                        | `linkedin-mcp-install add lobechat`              |

You can also drive the installer over HTTP via the `/api/install/*` routes
(see [INSTALL.md](install.md)).

---

## Claude Desktop (macOS / Linux / Windows)

- Docs: <https://docs.anthropic.com/en/docs/claude-desktop>
- Install:

  ```bash
  linkedin-mcp-install add claude-desktop-mac    # or -linux / -win
  ```

- Snippet (macOS):

  ```json
  {
    "mcpServers": {
      "linkedin-mcp-pro": {
        "command": "linkedin-mcp-pro",
        "args": ["serve"]
      }
    }
  }
  ```

- Troubleshooting: restart Claude Desktop after install. Verify with
  `linkedin-mcp-install verify claude-desktop-mac`.

## Claude Code

- Docs: <https://docs.anthropic.com/en/docs/claude-code>
- Install: `linkedin-mcp-install add claude-code`
- Same snippet as Claude Desktop; the install writes to `~/.claude.json`.

## Cursor

- Docs: <https://docs.cursor.com/welcome>
- Install: `linkedin-mcp-install add cursor`
- Restart Cursor. Open **Settings → Models → MCP** to confirm the server is
  listed.

## Cline (VS Code)

- Docs: <https://docs.cline.bot/mcp-servers/configuring-mcp-servers>
- Install: `linkedin-mcp-install add cline`
- Snippet goes into `~/.cline/mcp_settings.json`.

## Continue.dev

- Docs: <https://docs.continue.dev/customize/model-providers/mcp>
- Install: `linkedin-mcp-install add continue`

## Windsurf

- Docs: <https://docs.codeium.com/windsurf/mcp>
- Install: `linkedin-mcp-install add windsurf`

## Zed

- Docs: <https://zed.dev/docs/assistant/model-context-protocol>
- Note: Zed uses the `context_servers` key; the installer writes the correct
  shape automatically.
- Install: `linkedin-mcp-install add zed`

## Open WebUI

- Docs: <https://docs.openwebui.com/>
- Install: `linkedin-mcp-install add open-webui`

## LibreChat

- Docs: <https://www.librechat.ai/docs/configuration/mcp>
- Install: `linkedin-mcp-install add librechat`

## LobeChat

- Docs: <https://lobehub.com/docs/self-hosting/advanced/mcp>
- Install: `linkedin-mcp-install add lobechat`

---

## Generic / unsupported agents

Any MCP host that reads a JSON config with a `mcpServers` map is supported
manually — run `linkedin-mcp-install print-configs` to print snippets for
every supported agent, then paste the relevant block into your host's
config.
