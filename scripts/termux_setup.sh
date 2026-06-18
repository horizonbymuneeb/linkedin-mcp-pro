#!/usr/bin/env bash
# termux_setup.sh — set up an Android phone as a SOCKS proxy host so
# linkedin-mcp-pro on a remote server can browse via the phone's mobile IP.
#
# Why use a phone?
#   - Always with you, even when traveling
#   - Mobile IP rotates naturally \u2014 looks more like a real user
#   - Works as a backup when your laptop is off
#   - Some LinkedIn behavior changes are easier to test on mobile IP
#
# Requirements:
#   - Android 7+ phone
#   - Termux app (https://f-droid.org/packages/com.termux/)
#   - ~200 MB free space
#   - Mobile data or WiFi
#
# This script installs:
#   1. openssh (so EC2 can ssh into the phone as SOCKS proxy)
#   2. cloudflared (so the phone can expose a tunnel to EC2)
#   3. Sets up SSH key for passwordless auth
#   4. Creates a tmux session called "linkedin-tunnel" for persistence
#
# Usage (on the phone, inside Termux):
#   pkg install curl
#   curl -fsSL https://raw.githubusercontent.com/horizonbymuneeb/linkedin-mcp-pro/main/scripts/termux_setup.sh | bash
#
# Or manually:
#   git clone https://github.com/horizonbymuneeb/linkedin-mcp-pro
#   cd linkedin-mcp-pro
#   bash scripts/termux_setup.sh
#
# After setup, on the EC2 server, run scripts/bootstrap_session.sh and use
# PHONE_HOST and PHONE_PORT env vars to route through the phone.

set -euo pipefail

# ─── Pretty output ───────────────────────────────────────────────────────────
if [ -t 1 ]; then
  C_BOLD="\033[1m"; C_DIM="\033[2m"; C_GREEN="\033[32m"
  C_YELLOW="\033[33m"; C_RED="\033[31m"; C_RESET="\033[0m"
else
  C_BOLD=""; C_DIM=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_RESET=""
fi
log()    { echo -e "${C_DIM}[$(date +%H:%M:%S)]${C_RESET} $*"; }
info()   { echo -e "${C_BOLD}$*${C_RESET}"; }
ok()     { echo -e "${C_GREEN}\u2713${C_RESET} $*"; }
warn()   { echo -e "${C_YELLOW}\u26a0${C_RESET} $*"; }
die()    { echo -e "${C_RED}\u2717${C_RESET} $*" >&2; exit 1; }

# ─── Sanity check: are we in Termux? ─────────────────────────────────────────
if [ ! -d "/data/data/com.termux" ] && [ -z "${TERMUX_VERSION:-}" ]; then
  warn "This doesn't look like Termux."
  warn "  Install Termux from https://f-droid.org/packages/com.termux/"
  warn "  Then run this script from inside Termux."
  read -rp "Continue anyway? [y/N] " ans
  [[ "$ans" =~ ^[Yy]$ ]] || exit 1
fi

# ─── Update + install packages ──────────────────────────────────────────────
log "Updating package lists..."
pkg update -y

log "Installing openssh, tmux, rsync, curl, python..."
pkg install -y openssh tmux rsync curl python

# ─── cloudflared ────────────────────────────────────────────────────────────
install_cloudflared() {
  if command -v cloudflared >/dev/null 2>&1; then
    ok "cloudflared already installed"
    return
  fi
  log "Installing cloudflared..."
  local arch
  arch="$(uname -m)"
  case "$arch" in
    aarch64|arm64) arch="arm64" ;;
    armv7l|armv8l) arch="arm" ;;
    x86_64)        arch="amd64" ;;
    i686|i386)     arch="386" ;;
    *) die "Unsupported arch: $arch" ;;
  esac
  curl -fsSL -o "$PREFIX/bin/cloudflared" \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$arch"
  chmod +x "$PREFIX/bin/cloudflared"
  ok "cloudflared installed: $(cloudflared --version 2>&1 | head -1)"
}

# ─── Storage permission (for Termux) ─────────────────────────────────────────
request_storage() {
  if [ ! -d "/sdcard" ] || [ ! -w "/sdcard" ]; then
    warn "Granting storage permission..."
    termux-setup-storage
    sleep 2
  fi
}

# ─── SSH keypair for server → phone ──────────────────────────────────────────
setup_ssh_key() {
  mkdir -p "$HOME/.ssh"
  chmod 700 "$HOME/.ssh"
  if [ ! -f "$HOME/.ssh/linkedin_proxy_key" ]; then
    log "Generating SSH keypair for the server..."
    ssh-keygen -t ed25519 -N "" -f "$HOME/.ssh/linkedin_proxy_key" \
      -C "linkedin-mcp-pro@$(date +%Y-%m-%d)"
    ok "Keypair created at $HOME/.ssh/linkedin_proxy_key"
  else
    ok "Keypair already exists"
  fi

  # Authorize whatever public key the server will present
  AUTHORIZED="$HOME/.ssh/authorized_keys"
  touch "$AUTHORIZED"
  chmod 600 "$AUTHORIZED"

  echo
  info "${C_BOLD}Public key (paste this on the server):${C_RESET}"
  echo
  cat "$HOME/.ssh/linkedin_proxy_key.pub"
  echo
}

# ─── Configure sshd ─────────────────────────────────────────────────────────
configure_sshd() {
  mkdir -p "$HOME/.ssh"
  cat > "$PREFIX/etc/ssh/sshd_config" <<'EOF'
Port 8022
AddressFamily any
ListenAddress 0.0.0.0
PasswordAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
EOF
  ok "sshd configured on port 8022 (no password, key only)"
}

# ─── Start sshd in tmux ─────────────────────────────────────────────────────
start_sshd() {
  if pgrep -f "sshd" >/dev/null 2>&1; then
    ok "sshd already running"
  else
    log "Starting sshd in tmux session 'linkedin-ssh'..."
    tmux kill-session -t linkedin-ssh 2>/dev/null || true
    tmux new-session -d -s linkedin-ssh "sshd -E $HOME/.ssh/sshd.log"
    sleep 1
    if pgrep -f "sshd" >/dev/null 2>&1; then
      ok "sshd started (port 8022, tmux session 'linkedin-ssh')"
    else
      die "sshd failed to start. Check $HOME/.ssh/sshd.log"
    fi
  fi
}

# ─── tmux helpers ───────────────────────────────────────────────────────────
create_termux_proxy_script() {
  cat > "$PREFIX/bin/linkedin-proxy" <<'EOF'
#!/usr/bin/env bash
# Start a SOCKS5 proxy on the phone that EC2 can connect to.
# Usage:  Termux-side:  cloudflared access ssh --hostname myhost.trycloudflare.com --listener localhost:2222
#         EC2-side:     ssh -D 1080 -N -i ~/.ssh/linkedin_proxy_key -p 2222 phoneuser@localhost

set -euo pipefail
case "${1:-}" in
  start)
    tmux kill-session -t linkedin-tunnel 2>/dev/null || true
    if [ -z "${TUNNEL_URL:-}" ]; then
      echo "TUNNEL_URL not set. Either:"
      echo "  1. Run 'cloudflared tunnel --url ssh://localhost:8022' to get a fresh trycloudflare URL"
      echo "  2. Or set TUNNEL_URL to your named tunnel: export TUNNEL_URL=my.trycloudflare.com"
      exit 1
    fi
    tmux new-session -d -s linkedin-tunnel \
      "cloudflared access ssh --hostname $TUNNEL_URL --listener localhost:2222"
    sleep 2
    echo "tunnel up. EC2 should run:"
    echo "  ssh -D 1080 -N -i ~/.ssh/linkedin_proxy_key -p 2222 $(whoami)@localhost"
    ;;
  stop)
    tmux kill-session -t linkedin-tunnel 2>/dev/null || true
    echo "tunnel stopped"
    ;;
  status)
    tmux ls 2>/dev/null || echo "no sessions"
    ;;
  ip)
    # Print current mobile IP (so EC2 can confirm it changed)
    curl -fs ifconfig.me 2>/dev/null || echo "offline"
    ;;
  *)
    echo "Usage: linkedin-proxy {start|stop|status|ip}"
    exit 1
    ;;
esac
EOF
  chmod +x "$PREFIX/bin/linkedin-proxy"
  ok "linkedin-proxy command installed at $PREFIX/bin/linkedin-proxy"
}

# ─── Quick cloudflared one-shot tunnel ──────────────────────────────────────
quick_tunnel_instructions() {
  cat <<'EOF'

${C_BOLD}Quick start (no Cloudflare account needed):${C_RESET}
  1. Start a one-shot cloudflared tunnel:
       cloudflared tunnel --url ssh://localhost:8022
  2. It prints a URL like: https://random-words.trycloudflare.com
  3. Save it: export TUNNEL_URL=random-words.trycloudflare.com
  4. Start the proxy: linkedin-proxy start

${C_BOLD}Long-term (named tunnel, stable URL):${C_RESET}
  See docs/TERMUX_SETUP.md for cloudflared named tunnel setup.
EOF
}

# ─── Main ───────────────────────────────────────────────────────────────────
main() {
  info "linkedin-mcp-pro — Termux phone setup"
  info "======================================"
  echo

  request_storage
  install_cloudflared
  configure_sshd
  setup_ssh_key
  start_sshd
  create_termux_proxy_script

  echo
  ok "Setup complete!"
  echo
  info "What you have now:"
  echo "  - sshd on port 8022, key-only auth"
  echo "  - cloudflared installed"
  echo "  - 'linkedin-proxy' helper command"
  echo
  quick_tunnel_instructions
  echo
  info "Next: paste the public key above into your server's ~/.ssh/authorized_keys"
  info "Then on the server, use PHONE_HOST=localhost PHONE_PORT=2222 with bootstrap_session.sh"
}

main "$@"
