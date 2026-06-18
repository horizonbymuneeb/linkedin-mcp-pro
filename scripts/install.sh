# linkedin-mcp-pro

Python 3.11+ is required. Tested on 3.13.

For Docker / systemd setup, see [README.md](../README.md).

## Quick install

```bash
# 1. Get your li_at cookie (see README.md#getting-your-li_at-cookie)
LI_AT='your-cookie-here'

# 2. Create data dir
sudo mkdir -p /etc/linkedin-mcp-pro
echo "$LI_AT" | sudo tee /etc/linkedin-mcp-pro/li_at > /dev/null
sudo chmod 600 /etc/linkedin-mcp-pro/li_at
sudo chown root:root /etc/linkedin-mcp-pro/li_at

# 3. Clone and install
cd ~
git clone https://github.com/your-org/linkedin-mcp-pro.git
cd linkedin-mcp-pro
python3 -m venv .venv
.venv/bin/pip install -e .

# 4. Health check
.venv/bin/linkedin-mcp-health
```

If all green, point your MCP client (Claude Desktop, Cursor, Windsurf, etc.) at the `linkedin-mcp-pro` command in the venv.
