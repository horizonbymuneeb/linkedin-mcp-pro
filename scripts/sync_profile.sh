#!/usr/bin/env bash
# sync_profile.sh — re-sync the server's profile with a fresh copy from your
# laptop. Use this when:
#   - LinkedIn forces a security challenge (captcha, 2FA)
#   - You logged out and back in
#   - You want to update the profile (new Chrome extensions, prefs, etc.)
#
# This is a thin wrapper around the same logic as bootstrap_session.sh but
# with a friendlier error message and a "did the session work?" check at the
# end (optional, opt-in).
#
# Usage:
#   ./scripts/sync_profile.sh                    # sync only
#   POST_CHECK=1 ./scripts/sync_profile.sh       # sync + post a test message

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Bootstrap is the heavy lifter; just delegate
exec "$SCRIPT_DIR/bootstrap_session.sh" "$@"
