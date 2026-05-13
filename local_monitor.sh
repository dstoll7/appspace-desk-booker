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

# Check last keep-alive run status
LAST_STATUS=$(gh run list --workflow="keep-alive.yml" --repo "$REPO" --limit 1 --json conclusion -q '.[0].conclusion' 2>/dev/null)

if [ "$LAST_STATUS" = "success" ]; then
    # All good - clean up any stale lock
    rm -f "$LOCKFILE"
    exit 0
fi

# Double-check by hitting the API directly (in case GH Actions has a delay)
TOKEN=$(gh secret list --repo "$REPO" 2>/dev/null | grep -c APPSPACE_SESSION_TOKEN)
if [ "$TOKEN" -eq 0 ]; then
    osascript -e 'display notification "No APPSPACE_SESSION_TOKEN secret found" with title "Appspace Monitor Error"'
    exit 1
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
