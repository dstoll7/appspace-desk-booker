#!/bin/bash
# Wrapper launchd runs instead of book_week.py directly. It adds the things a
# bare python invocation can't do:
#
#   1. Network wait — after a wake-from-sleep, Wi-Fi often isn't up for a few
#      seconds. book_week.py would hit ERR_INTERNET_DISCONNECTED and die. We poll
#      the Appspace host with backoff for up to ~10 min before starting.
#
#   2. Selective failure notification — book_week.py needs an Okta push approved
#      almost every workday morning (the 7-day booking window rolls forward one
#      day at a time, and the token dies ~1h after login, so you can never get
#      more than the current window ahead). launchd fires this on wake, so most
#      mornings the browser opens the instant you lift the lid and waits ~3 min
#      for your tap. If you're not ready, book_week.py exits EXIT_NEEDS_APPROVAL
#      — that's EXPECTED, not a failure, so we stay silent. We only raise a
#      macOS notification for a GENUINE error (no network, or a real failure
#      after you authenticated), so the alert means something again.
#
# The idempotency guard lives in book_week.py: once every bookable day is booked
# it exits 0 without opening a browser, so this wrapper can be scheduled many
# times a day harmlessly.

set -uo pipefail

REPO_DIR="/Users/daniel.stoll/GitHub/appspace-desk-booker"
PYTHON="$REPO_DIR/.venv/bin/python"
HOST="https://disney.cloud.appspace.com"

# Keep in sync with book_week.py's EXIT_NEEDS_APPROVAL: "browser opened but the
# Okta login wasn't completed in time" — benign, no notification.
EXIT_NEEDS_APPROVAL=3

notify() {
    # $1 = title, $2 = message
    /usr/bin/osascript -e "display notification \"$2\" with title \"$1\" sound name \"Basso\"" 2>/dev/null || true
}

# Poll the Appspace host with backoff. Returns 0 once reachable, 1 if it never
# comes up within ~10 min (20 tries, 30s apart).
wait_for_network() {
    local i
    for i in $(seq 1 20); do
        if /usr/bin/curl -s -o /dev/null --max-time 10 "$HOST"; then
            return 0
        fi
        sleep 30
    done
    return 1
}

# Decide what to do with book_week.py's exit status. Notify ONLY on a genuine
# failure. Returns the status so the caller can propagate it.
handle_exit_status() {
    local status="$1"
    if [ "$status" -eq 0 ]; then
        return 0
    fi
    if [ "$status" -eq "$EXIT_NEEDS_APPROVAL" ]; then
        echo "$(date): Okta login not completed in time — nothing booked, no action needed. A later run (or you) will book it."
        return 0
    fi
    notify "Desk booking FAILED" "book_week.py exited $status — a real error (not a missed Okta tap). Run it manually and check ~/Library/Logs/appspace-book-week.log."
    return "$status"
}

main() {
    if ! wait_for_network; then
        echo "$(date): network never came up after ~10 min — aborting this run."
        notify "Desk booking skipped" "No network after 10 min. It'll retry on the next scheduled run."
        exit 1
    fi

    cd "$REPO_DIR" || exit 1
    "$PYTHON" book_week.py
    local status=$?

    handle_exit_status "$status"
    exit $?
}

# Only run main when executed directly (launchd). When sourced by a test,
# BASH_SOURCE[0] != $0, so the functions above are defined without side effects.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    main "$@"
fi
