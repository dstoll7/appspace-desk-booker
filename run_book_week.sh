#!/bin/bash
# Wrapper launchd runs instead of book_week.py directly. It adds the two things
# a bare python invocation can't do:
#
#   1. Network wait — after a wake-from-sleep, Wi-Fi often isn't up for a few
#      seconds. book_week.py would hit ERR_INTERNET_DISCONNECTED and die. We poll
#      the Appspace host with backoff for up to ~10 min before starting.
#
#   2. Failure notification — if the run exits non-zero (no network, or the Okta
#      push was never approved so no token was captured), we surface a macOS
#      notification with sound. No more silent lost weeks.
#
# The idempotency guard lives in book_week.py: once the week is booked it exits 0
# without opening a browser, so this wrapper can be scheduled multiple times a day
# harmlessly.

set -uo pipefail

REPO_DIR="/Users/daniel.stoll/GitHub/appspace-desk-booker"
PYTHON="$REPO_DIR/.venv/bin/python"
HOST="https://disney.cloud.appspace.com"

notify() {
    # $1 = title, $2 = message
    /usr/bin/osascript -e "display notification \"$2\" with title \"$1\" sound name \"Basso\"" 2>/dev/null || true
}

# --- 1. Wait for network (up to ~10 min: 20 tries, 30s apart) -----------------
connected=0
for i in $(seq 1 20); do
    if /usr/bin/curl -s -o /dev/null --max-time 10 "$HOST"; then
        connected=1
        break
    fi
    sleep 30
done

if [ "$connected" -ne 1 ]; then
    echo "$(date): network never came up after ~10 min — aborting this run."
    notify "Desk booking skipped" "No network after 10 min. It'll retry on the next scheduled run."
    exit 1
fi

# --- 2. Run the booker --------------------------------------------------------
cd "$REPO_DIR" || exit 1
"$PYTHON" book_week.py
status=$?

# --- 3. Notify on failure -----------------------------------------------------
if [ "$status" -ne 0 ]; then
    notify "Desk booking FAILED" "book_week.py exited $status. Likely the Okta push wasn't approved. Run it manually to book your week."
fi

exit "$status"
