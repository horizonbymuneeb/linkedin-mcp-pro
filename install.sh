#!/usr/bin/env bash
# LinkedIn MCP Pro - one-line installer (macOS / Linux)
# Usage: curl -fsSL https://raw.githubusercontent.com/horizonbymuneeb/linkedin-mcp-pro/main/install.sh | bash

set -euo pipefail

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

# --- Detect OS ---------------------------------------------------------------
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
case "$OS" in
  darwin) OS_LABEL="macOS" ;;
  linux)  OS_LABEL="Linux" ;;
  *) die "Unsupported OS: $OS" ;;
esac
say "Detected platform: $OS_LABEL"

# --- Check Python ------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  die "python3 not found. Install Python 3.11+ and re-run this script."
fi

PY_VERSION="$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
PY_MAJOR="$(echo "$PY_VERSION" | cut -d. -f1)"
PY_MINOR="$(echo "$PY_VERSION" | cut -d. -f2)"
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
  die "Python $PY_VERSION found, but 3.11+ is required."
fi
say "Python $PY_VERSION OK"

# --- Install package ---------------------------------------------------------
if command -v pipx >/dev/null 2>&1; then
  say "Installing with pipx"
  pipx install linkedin-mcp-pro || pipx upgrade linkedin-mcp-pro || true
elif python3 -m pip --version >/dev/null 2>&1; then
  say "Installing with pip (user)"
  python3 -m pip install --user --upgrade linkedin-mcp-pro
else
  die "Neither pipx nor pip is available. Install pip and re-run."
fi

# --- Ensure ~/.local/bin is on PATH for this shell ---------------------------
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) warn "$HOME/.local/bin is not on PATH. Add it: export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

# --- Create profile dir ------------------------------------------------------
PROFILE_DIR="$HOME/.linkedin-mcp/profile"
mkdir -p "$PROFILE_DIR"
say "Profile directory ready: $PROFILE_DIR"

# --- Print next steps --------------------------------------------------------
cat <<EOF

Installation complete.

Next steps:
  1. Configure an MCP agent:
       linkedin-mcp-install add claude-desktop-$([ "$OS" = "darwin" ] && echo mac || echo linux)
  2. (Optional) provide a LinkedIn session cookie:
       export LI_AT="<your li_at cookie>"
  3. Verify:
       linkedin-mcp-install doctor

EOF

# --- Show config snippets for the most common agents on this OS --------------
say "Config snippets for $OS_LABEL:"
case "$OS" in
  darwin) AGENT_IDS="claude-desktop-mac cursor" ;;
  linux)  AGENT_IDS="claude-desktop-linux cursor" ;;
esac
for agent in $AGENT_IDS; do
  echo
  echo "### $agent"
  if command -v linkedin-mcp-install >/dev/null 2>&1; then
    linkedin-mcp-install print-configs 2>/dev/null | awk -v a="$agent" '
      $0 ~ "^### " { in_block = ($0 == "### "a) }
      in_block { print }
    '
  else
    echo "(run 'linkedin-mcp-install print-configs' to view snippets)"
  fi
done
