#!/bin/bash
# Auto-refresh monitor: checks if Appspace token is alive.
# If dead, automatically launches the refresh browser for re-auth.
# You just need to approve the Okta push notification.
#
# Install as launchd job:
#   cp com.dstoll.appspace-monitor.plist ~/Library/LaunchAgents/
#   launchctl load ~/Library/LaunchAgents/com.dstoll.appspace-monitor.plist

REPO="dstoll7/appspace-desk-booker"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python3"
LOCKFILE="/tmp/.appspace-refresh-running"

# There is no keep-alive workflow anymore. Tokens are minted on-demand by the
# booking/check-in workflows. The local machine only needs to act in ONE case:
# when a headless mint failed because the Okta session in PLAYWRIGHT_AUTH_STATE
# expired — CI signals that by opening a GitHub Issue labeled "token-expired".
OPEN_ISSUE=$(gh issue list --label "token-expired" --state open \
  --repo "$REPO" --json number -q '.[0].number' 2>/dev/null)

if [ $? -ne 0 ]; then
    echo "Could not query GitHub issues — skipping to avoid false positive"
    exit 0
fi

if [ -z "$OPEN_ISSUE" ]; then
    # No open issue — CI is minting tokens fine. Nothing to do.
    rm -f "$LOCKFILE"
    exit 0
fi

# Don't run if already refreshing
if [ -f "$LOCKFILE" ]; then
    AGE=$(( $(date +%s) - $(stat -f %m "$LOCKFILE") ))
    # If lock is older than 5 minutes, it's stale
    if [ "$AGE" -lt 300 ]; then
        exit 0
    fi
    rm -f "$LOCKFILE"
fi

# Don't auto-launch outside work hours (8 AM - 8 PM ET)
HOUR=$(TZ="America/New_York" date +%H)
if [ "$HOUR" -lt 8 ] || [ "$HOUR" -gt 20 ]; then
    exit 0
fi

# Token is dead - auto-launch refresh
touch "$LOCKFILE"

osascript -e 'display notification "Opening browser for Okta re-auth. Approve the push notification." with title "Appspace Token Expired" subtitle "Auto-refreshing..."'

# Wait a moment for notification to show
sleep 2

# Run the refresh script (opens browser, user just approves Okta push)
"$PYTHON" "$SCRIPT_DIR/refresh_token.py" --headed 2>/tmp/appspace-refresh.log

if [ $? -eq 0 ]; then
    osascript -e 'display notification "Token refreshed successfully! Desk booking is working again." with title "Appspace Token Restored" sound name "Glass"'
else
    osascript -e 'display notification "Auto-refresh failed. Check /tmp/appspace-refresh.log" with title "Appspace Refresh Failed" sound name "Basso"'
fi

rm -f "$LOCKFILE"
