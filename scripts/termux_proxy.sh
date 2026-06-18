#!/usr/bin/env bash
# termux_proxy.sh — convenience wrapper for the linkedin-proxy command.
# This file is installed at $PREFIX/bin/linkedin-proxy by termux_setup.sh
# so it lives both in the repo (for reference / source-of-truth) and on
# the phone (for actual use).
#
# Usage on the phone:
#   linkedin-proxy start    # start the cloudflared tunnel
#   linkedin-proxy stop     # stop the tunnel
#   linkedin-proxy status   # show tmux sessions
#   linkedin-proxy ip       # print current mobile IP
#
# Prereq: termux_setup.sh must have been run first (sets up sshd, keypair,
# cloudflared, tmux). TUNNEL_URL env var must be set.

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
    echo "  ssh -D 1080 -N -i ~/.ssh/phone_key -p 2222 $(whoami)@localhost"
    ;;
  stop)
    tmux kill-session -t linkedin-tunnel 2>/dev/null || true
    echo "tunnel stopped"
    ;;
  status)
    tmux ls 2>/dev/null || echo "no tmux sessions"
    ;;
  ip)
    curl -fs ifconfig.me 2>/dev/null || echo "offline"
    ;;
  *)
    echo "Usage: linkedin-proxy {start|stop|status|ip}"
    exit 1
    ;;
esac
