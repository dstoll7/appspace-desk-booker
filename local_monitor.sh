#!/bin/bash
# Local monitor: checks if Appspace token is alive via GitHub Actions.
# If the last keep-alive failed, sends a macOS notification prompting re-auth.
#
# Install as launchd job:
#   cp com.dstoll.appspace-monitor.plist ~/Library/LaunchAgents/
#   launchctl load ~/Library/LaunchAgents/com.dstoll.appspace-monitor.plist

REPO="dstoll7/appspace-desk-booker"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Check last keep-alive run status
LAST_STATUS=$(gh run list --workflow="keep-alive.yml" --repo "$REPO" --limit 1 --json conclusion -q '.[0].conclusion' 2>/dev/null)

if [ "$LAST_STATUS" = "success" ]; then
    exit 0
fi

# Check if we've already notified recently (within 2 hours)
NOTIFY_FLAG="/tmp/.appspace-notified"
if [ -f "$NOTIFY_FLAG" ]; then
    AGE=$(( $(date +%s) - $(stat -f %m "$NOTIFY_FLAG") ))
    if [ "$AGE" -lt 7200 ]; then
        exit 0
    fi
fi

# Token is dead - notify user
osascript -e 'display notification "Run: python refresh_token.py --headed" with title "Appspace Token Expired" subtitle "Desk booking is broken until you re-auth"'

touch "$NOTIFY_FLAG"
