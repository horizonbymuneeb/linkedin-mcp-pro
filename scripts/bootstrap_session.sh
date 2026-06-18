#!/usr/bin/env bash
# bootstrap_session.sh — one-time setup: copy your laptop's Chrome profile to
# the server so linkedin-mcp-pro can post as you without needing fresh cookies.
#
# Flow:
#   1. Detect OS + Chrome profile location
#   2. Verify you are logged into LinkedIn in Chrome
#   3. Package relevant files (Cookies, Local Storage, Local State, sessions)
#   4. Transfer to server via one of:
#        a. Direct scp/rsync to EC2 public IP (if Security Group allows)
#        b. Rsync over the existing cloudflared SSH tunnel
#        c. Manual: print instructions, you copy yourself
#
# Re-run anytime to refresh the session.
#
# Usage:
#   ./scripts/bootstrap_session.sh                         # auto-detect, use default tunnel
#   EC2_HOST=admin@1.2.3.4 ./scripts/bootstrap_session.sh # explicit EC2
#   TUNNEL_URL=my.trycloudflare.com ./scripts/bootstrap_session.sh
#   SKIP_POST_CHECK=1 ./scripts/bootstrap_session.sh      # don't verify by posting
#
# Requirements: bash, rsync, ssh, jq (for server-side profile path detection)

set -euo pipefail

# ─── Configuration ───────────────────────────────────────────────────────────
EC2_HOST="${EC2_HOST:-admin@13.53.68.179}"      # override if needed
TUNNEL_URL="${TUNNEL_URL:-are-vbulletin-purchases-dat.trycloudflare.com}"
TUNNEL_PORT="${TUNNEL_PORT:-2222}"                # cloudflared listener on EC2
LAPTOP_SSH_PORT="${LAPTOP_SSH_PORT:-22}"
PROFILE_DIR_REMOTE="${PROFILE_DIR_REMOTE:-/home/admin/.linkedin-mcp/profile}"
LOG_LEVEL="${LOG_LEVEL:-info}"

# ─── Pretty output ───────────────────────────────────────────────────────────
if [ -t 1 ]; then
  C_BOLD="\033[1m"; C_DIM="\033[2m"; C_GREEN="\033[32m"
  C_YELLOW="\033[33m"; C_RED="\033[31m"; C_RESET="\033[0m"
else
  C_BOLD=""; C_DIM=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_RESET=""
fi
log()    { echo -e "${C_DIM}[$(date +%H:%M:%S)]${C_RESET} $*"; }
info()   { echo -e "${C_BOLD}$*${C_RESET}"; }
ok()     { echo -e "${C_GREEN}✓${C_RESET} $*"; }
warn()   { echo -e "${C_YELLOW}⚠${C_RESET} $*"; }
die()    { echo -e "${C_RED}✗${C_RESET} $*" >&2; exit 1; }

# ─── Detect OS + Chrome profile ─────────────────────────────────────────────
detect_chrome_profile() {
  case "$(uname -s)" in
    Darwin)
      CHROME_PROFILE="$HOME/Library/Application Support/Google/Chrome/Default"
      [ -d "$CHROME_PROFILE" ] || CHROME_PROFILE="$HOME/Library/Application Support/Google/Chrome/Profile 1"
      ;;
    Linux)
      CHROME_PROFILE="$HOME/.config/google-chrome/Default"
      [ -d "$CHROME_PROFILE" ] || CHROME_PROFILE="$HOME/.config/google-chrome/Profile 1"
      ;;
    MINGW*|CYGWIN*|MSYS*)
      die "Windows native: use WSL. Run this from inside WSL."
      ;;
    *)
      die "Unknown OS: $(uname -s). Use Linux/macOS or WSL."
      ;;
  esac

  if [ ! -d "$CHROME_PROFILE" ]; then
    die "Chrome profile not found at: $CHROME_PROFILE
  Open Chrome, log into LinkedIn, then re-run this script."
  fi
  ok "Chrome profile: $CHROME_PROFILE"

  # Verify Local State (contains the decryption key for Cookies DB)
  if [ ! -f "$CHROME_PROFILE/Local State" ]; then
    die "Local State not found in $CHROME_PROFILE — is this a real Chrome profile?"
  fi
  ok "Local State present (decryption key will sync with cookies)"
}

# ─── Verify LinkedIn login ───────────────────────────────────────────────────
verify_linkedin_login() {
  local cookies_db="$CHROME_PROFILE/Cookies"
  if [ ! -f "$cookies_db" ]; then
    die "No Cookies database at $cookies_db — has Chrome been opened at all?"
  fi

  log "Looking for li_at cookie (this checks the encrypted Cookies DB)..."
  if command -v sqlite3 >/dev/null 2>&1; then
    local found
    found="$(sqlite3 "$cookies_db" "SELECT count(*) FROM cookies WHERE host_key='.linkedin.com' AND name='li_at'")"
    if [ "$found" -gt 0 ]; then
      ok "li_at cookie present in Chrome — you are logged in to LinkedIn"
      return 0
    fi
  fi

  warn "Could not confirm li_at via sqlite (may be encrypted, or sqlite3 missing)"
  warn "If you have LinkedIn open in Chrome, proceed. Otherwise open it first."
  read -rp "Continue anyway? [y/N] " ans
  [[ "$ans" =~ ^[Yy]$ ]] || die "Aborted. Open linkedin.com in Chrome, then re-run."
}

# ─── Stage profile for transfer ─────────────────────────────────────────────
stage_profile() {
  STAGE_DIR="$(mktemp -d -t li-profile-XXXXXX)"
  trap 'rm -rf "$STAGE_DIR"' EXIT

  log "Staging profile files into $STAGE_DIR ..."
  # Copy only what we need — saves bandwidth, avoids leaking history/bookmarks
  local files=(
    "Cookies"
    "Cookies-journal"
    "Local State"
    "Login Data"
    "Web Data"
    "Preferences"
    "Secure Preferences"
    "Network"
  )
  local dirs=(
    "Local Storage"
    "Session Storage"
    "File System"
    "Service Worker"
    "Extension State"
    "Extension Scripts"
  )

  for f in "${files[@]}"; do
    if [ -e "$CHROME_PROFILE/$f" ]; then
      cp -p "$CHROME_PROFILE/$f" "$STAGE_DIR/"
    fi
  done
  for d in "${dirs[@]}"; do
    if [ -d "$CHROME_PROFILE/$d" ]; then
      cp -Rp "$CHROME_PROFILE/$d" "$STAGE_DIR/"
    fi
  done

  local size
  size=$(du -sh "$STAGE_DIR" | cut -f1)
  ok "Profile staged: $STAGE_DIR ($size)"
}

# ─── Transfer methods ───────────────────────────────────────────────────────

# Method 1: direct scp to EC2's public IP (works if EC2 SG allows 22 from your IP)
try_direct_scp() {
  log "Trying direct scp to $EC2_HOST ..."
  if ssh -o ConnectTimeout=5 -o BatchMode=yes "$EC2_HOST" true 2>/dev/null; then
    info "→ direct scp available, using it (fastest)"
    rsync -avz --progress \
      -e "ssh -o StrictHostKeyChecking=accept-new" \
      "$STAGE_DIR/" "$EC2_HOST:$PROFILE_DIR_REMOTE/"
    return 0
  else
    return 1
  fi
}

# Method 2: rsync over existing cloudflared SSH tunnel
#   The tunnel runs: EC2 localhost:2222 ← cloudflared ← laptop's sshd :22
#   Direction EC2→laptop: `ssh -p 2222 alian@localhost` from EC2
#   Direction laptop→EC2: direct scp to 13.53.68.179 (covered by method 1)
#   If direct scp is blocked, we use a reverse-pull: from EC2, rsync from us.
try_reverse_rsync() {
  log "Trying reverse rsync (server pulls profile from us)..."
  if ! command -v sshd >/dev/null && ! pgrep -f "sshd" >/dev/null; then
    warn "sshd not running on this laptop — can't accept reverse pull"
    return 1
  fi

  local laptop_user
  laptop_user="$(whoami)"
  local laptop_ip
  laptop_ip="$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")"

  info "→ reverse pull: server will ssh to ${laptop_user}@${laptop_ip}:${LAPTOP_SSH_PORT}"
  info "  Make sure laptop sshd allows ${laptop_user} from the server"

  if ! ssh -o ConnectTimeout=5 -o BatchMode=yes \
       "${EC2_HOST%@*}@${TUNNEL_URL#*//}" -p "$TUNNEL_PORT" \
       "ssh -o ConnectTimeout=5 -o BatchMode=yes ${laptop_user}@${laptop_ip} -p ${LAPTOP_SSH_PORT} true" 2>/dev/null; then
    warn "Server cannot reach laptop sshd — last-resort: print manual instructions"
    return 2
  fi

  # Use the tunnel to push from server
  local remote_cmd="rsync -avz --progress -e 'ssh -p ${LAPTOP_SSH_PORT} -o StrictHostKeyChecking=accept-new' \
    ${laptop_user}@${laptop_ip}:${STAGE_DIR}/\\* $PROFILE_DIR_REMOTE/"
  ssh -o ConnectTimeout=5 -o BatchMode=yes \
     "${EC2_HOST%@*}@${TUNNEL_URL#*//}" -p "$TUNNEL_PORT" "$remote_cmd"
  return 0
}

# Method 3: print manual instructions
print_manual_instructions() {
  cat <<EOF

${C_BOLD}Manual transfer instructions${C_RESET}
================================
Run this on your ${C_BOLD}server${C_RESET} (after you've copied the profile somehow):

${C_DIM}# Option A: from the staged dir, on your laptop:
rsync -avz -e "ssh" $STAGE_DIR/ $EC2_HOST:$PROFILE_DIR_REMOTE/

# Option B: tar and copy
tar czf - -C $STAGE_DIR . | ssh $EC2_HOST "tar xzf - -C $PROFILE_DIR_REMOTE/"

# Option C: from this terminal on the server, pull from your laptop
ssh $EC2_HOST "rsync -avz -e 'ssh -p $LAPTOP_SSH_PORT' $(whoami)@<your-laptop-ip>:$STAGE_DIR/ $PROFILE_DIR_REMOTE/"
EOF
}

# ─── Verify on server ───────────────────────────────────────────────────────
verify_on_server() {
  log "Verifying profile on server..."
  ssh "$EC2_HOST" "test -f '$PROFILE_DIR_REMOTE/Local State' && test -f '$PROFILE_DIR_REMOTE/Cookies' && echo OK || echo MISSING"
}

# ─── Main ────────────────────────────────────────────────────────────────────
main() {
  info "linkedin-mcp-pro — profile bootstrap"
  info "======================================"
  echo

  detect_chrome_profile
  verify_linkedin_login
  stage_profile

  echo
  info "Transferring profile to server..."

  local method_used=""
  if try_direct_scp 2>/dev/null; then
    method_used="direct scp"
  elif try_reverse_rsync; then
    method_used="reverse rsync over tunnel"
  else
    print_manual_instructions
    exit 1
  fi

  echo
  ok "Profile transferred via $method_used"
  verify_on_server

  echo
  info "${C_GREEN}Bootstrap complete!${C_RESET}"
  echo
  echo "Next steps (on the server):"
  echo "  1. linkedin-mcp-pro is ready to use — it will pick up the profile automatically"
  echo "  2. Or test directly: python3 $0/use_profile_session.py"
  echo "  3. Re-run this script whenever LinkedIn forces re-auth (~every 6 months)"
  echo
  echo "For more proxy/tunnel options, see: docs/PROXY_SETUP.md"
}

main "$@"
