#!/usr/bin/env python3
"""book_week.main() exit-code contract.

The wrapper (run_book_week.sh) uses these codes to decide whether to fire a
macOS "FAILED" notification:

  * EXIT_NEEDS_APPROVAL (3) — the browser opened but the Okta login wasn't
    completed in time. Expected/benign on a morning you haven't tapped the push
    yet; the wrapper stays SILENT (no notification).
  * 1 — a genuine failure AFTER auth (couldn't mint a session token, or a
    booking API call failed). The wrapper DOES notify.

Run: .venv/bin/python tests/test_exit_codes.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import book_week


def run_main(argv):
    """Invoke book_week.main() with argv, returning its exit code (0 if none)."""
    old_argv = sys.argv
    sys.argv = argv
    try:
        book_week.main()
        return 0
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    finally:
        sys.argv = old_argv


def test_no_token_exits_needs_approval():
    """capture_refresh_token() -> None means 'you didn't finish the Okta login'.
    That must be the benign EXIT_NEEDS_APPROVAL code, not a generic failure."""
    orig = book_week.capture_refresh_token
    book_week.capture_refresh_token = lambda: None
    try:
        code = run_main(["book_week.py", "--force"])
        assert code == book_week.EXIT_NEEDS_APPROVAL, (
            f"expected {book_week.EXIT_NEEDS_APPROVAL} (needs approval), got {code}"
        )
        print(f"PASS: no token -> exit {book_week.EXIT_NEEDS_APPROVAL}")
    finally:
        book_week.capture_refresh_token = orig


def test_mint_failure_exits_one():
    """A captured token that fails to mint a session is a GENUINE error -> exit 1."""
    orig_cap = book_week.capture_refresh_token
    orig_mint = book_week.mint_session_token
    book_week.capture_refresh_token = lambda: "fake-refresh-token"
    book_week.mint_session_token = lambda _t: None
    try:
        code = run_main(["book_week.py", "--force"])
        assert code == 1, f"expected 1 (mint failure), got {code}"
        print("PASS: mint failure -> exit 1")
    finally:
        book_week.capture_refresh_token = orig_cap
        book_week.mint_session_token = orig_mint


def test_transient_browser_error_exits_transient():
    """A flaky-network browser failure (ERR_NETWORK_CHANGED on wake, browser
    launch timeout) is infrastructure noise, not something to alert on. It must
    exit EXIT_TRANSIENT so the wrapper stays silent and a later run retries."""
    orig = book_week.capture_refresh_token

    def boom():
        raise book_week.TransientBrowserError(
            "Page.goto: net::ERR_NETWORK_CHANGED at https://disney.cloud.appspace.com/"
        )

    book_week.capture_refresh_token = boom
    try:
        code = run_main(["book_week.py", "--force"])
        assert code == book_week.EXIT_TRANSIENT, (
            f"expected {book_week.EXIT_TRANSIENT} (transient), got {code}"
        )
        print(f"PASS: transient browser error -> exit {book_week.EXIT_TRANSIENT}")
    finally:
        book_week.capture_refresh_token = orig


if __name__ == "__main__":
    test_no_token_exits_needs_approval()
    test_mint_failure_exits_one()
    test_transient_browser_error_exits_transient()
    print("ALL PASS")
