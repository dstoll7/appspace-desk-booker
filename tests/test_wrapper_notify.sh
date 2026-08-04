#!/bin/bash
# Contract for run_book_week.sh's handle_exit_status(): it must fire the macOS
# "FAILED" notification ONLY on a genuine failure — never on success and never
# on EXIT_NEEDS_APPROVAL (you just haven't tapped the Okta push yet).
#
# Run: bash tests/test_wrapper_notify.sh
set -uo pipefail
cd "$(dirname "$0")/.."

# Source the wrapper. The BASH_SOURCE guard means main() does NOT run on source,
# so no network wait / no python invocation happens here.
# shellcheck disable=SC1091
source ./run_book_week.sh

if ! type handle_exit_status >/dev/null 2>&1; then
    echo "FAIL: run_book_week.sh does not define handle_exit_status()"
    exit 1
fi

# Stub notify() to count calls instead of showing a real notification.
NOTIFY_CALLS=0
notify() { NOTIFY_CALLS=$((NOTIFY_CALLS + 1)); }

fail=0
check() { # $1 = description, $2 = expected notify count
    if [ "$NOTIFY_CALLS" -eq "$2" ]; then
        echo "PASS: $1 (notify calls=$NOTIFY_CALLS)"
    else
        echo "FAIL: $1 (notify calls=$NOTIFY_CALLS, expected $2)"
        fail=1
    fi
}

NOTIFY_CALLS=0; handle_exit_status 0 || true
check "exit 0 (success) => silent" 0

NOTIFY_CALLS=0; handle_exit_status "$EXIT_NEEDS_APPROVAL" || true
check "exit $EXIT_NEEDS_APPROVAL (Okta not completed) => silent" 0

NOTIFY_CALLS=0; handle_exit_status "$EXIT_TRANSIENT" || true
check "exit $EXIT_TRANSIENT (transient network/browser) => silent" 0

NOTIFY_CALLS=0; handle_exit_status 1 || true
check "exit 1 (genuine error) => notify once" 1

NOTIFY_CALLS=0; handle_exit_status 2 || true
check "exit 2 (other error) => notify once" 1

[ "$fail" -eq 0 ] && echo "ALL PASS"
exit "$fail"
