#!/usr/bin/env python3
"""
Appspace Token Refresher using Playwright.

Opens a browser to the Appspace login page, waits for Disney SSO authentication,
captures the session token from API responses, and updates the GitHub Secret.

First run: requires manual SSO login (browser opens visibly).
Subsequent runs: uses saved browser state to skip SSO (headless).

Usage:
    python refresh_token.py          # Auto-detect: headless if session exists, headed if not
    python refresh_token.py --headed # Force visible browser (for re-auth)
    python refresh_token.py --headless # Force headless (assumes valid session)
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

APPSPACE_URL = "https://disney.cloud.appspace.com"
STATE_DIR = Path(__file__).parent / ".playwright-state"
STATE_FILE = STATE_DIR / "auth-state.json"
GITHUB_REPO = "dstoll7/appspace-desk-booker"


def update_github_secret(name: str, value: str):
    """Update a GitHub Actions secret using the gh CLI."""
    result = subprocess.run(
        ["gh", "secret", "set", name, "--repo", GITHUB_REPO, "--body", value],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  ERROR updating secret: {result.stderr}")
        return False
    return True


def capture_token(headed: bool = False) -> dict | None:
    """Launch browser, navigate to Appspace, and capture the session token."""
    STATE_DIR.mkdir(exist_ok=True)

    has_state = STATE_FILE.exists()
    headless = not headed

    if not has_state and headless:
        print("No saved browser state found. Launching visible browser for SSO login...")
        headless = False

    print(f"Launching browser ({'headless' if headless else 'visible'})...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context_opts = {"viewport": {"width": 1280, "height": 800}}
        if has_state:
            context_opts["storage_state"] = str(STATE_FILE)

        context = browser.new_context(**context_opts)
        page = context.new_page()

        captured_token = {}

        def handle_response(response):
            """Intercept API responses to find the session token."""
            url = response.url
            # Capture token from any successful Appspace API call's request headers
            if "disney.cloud.appspace.com/api/" in url and response.status == 200:
                request = response.request
                token = request.headers.get("token")
                if token and len(token) > 20:
                    captured_token["session_token"] = token

            # Also capture from authorization/token response body
            if "authorization/token" in url and response.status == 200:
                try:
                    body = response.json()
                    if body.get("accessToken"):
                        captured_token["session_token"] = body["accessToken"]
                    if body.get("refreshToken"):
                        captured_token["refresh_token"] = body["refreshToken"]
                except Exception:
                    pass

        page.on("response", handle_response)

        print(f"Navigating to {APPSPACE_URL}...")
        page.goto(APPSPACE_URL, wait_until="domcontentloaded")

        # Wait for SSO redirect and authentication
        if not headless:
            print("\n" + "=" * 60)
            print("Please complete the Disney SSO login in the browser window.")
            print("The script will automatically detect when you're logged in.")
            print("=" * 60 + "\n")

        # Wait until we land on the Appspace app (post-login)
        try:
            page.wait_for_url("**/disney.cloud.appspace.com/**", timeout=120_000)
        except PlaywrightTimeout:
            pass

        # If we're on the SSO page, wait for the user to complete login
        max_wait = 300 if not headless else 30
        waited = 0
        while waited < max_wait:
            current_url = page.url
            if "disney.cloud.appspace.com" in current_url and "/login" not in current_url:
                break
            time.sleep(2)
            waited += 2

        if waited >= max_wait:
            print("ERROR: Timed out waiting for login to complete.")
            browser.close()
            return None

        print("Logged in! Waiting for API calls to capture token...")

        # Navigate to reservations page to trigger API calls with the token
        time.sleep(2)
        page.goto(f"{APPSPACE_URL}/reservations", wait_until="networkidle")
        time.sleep(3)

        # If we still don't have a token, try triggering an API call
        if not captured_token.get("session_token"):
            try:
                page.goto(f"{APPSPACE_URL}/api/v3/reservation/users/me/events?limit=1",
                          wait_until="networkidle")
                time.sleep(2)
            except Exception:
                pass

        # Last resort: check localStorage/sessionStorage
        if not captured_token.get("session_token"):
            try:
                token = page.evaluate("""() => {
                    return localStorage.getItem('token') ||
                           localStorage.getItem('sessionToken') ||
                           localStorage.getItem('access_token') ||
                           sessionStorage.getItem('token') ||
                           sessionStorage.getItem('sessionToken') ||
                           '';
                }""")
                if token:
                    captured_token["session_token"] = token
            except Exception:
                pass

        # Check cookies too
        if not captured_token.get("session_token"):
            cookies = context.cookies()
            for cookie in cookies:
                if cookie["name"].lower() in ("token", "session_token", "appspace_token"):
                    captured_token["session_token"] = cookie["value"]
                    break

        # Save browser state for next time (skip SSO)
        context.storage_state(path=str(STATE_FILE))
        print("Browser state saved for future headless runs.")

        browser.close()

    return captured_token if captured_token.get("session_token") else None


def main():
    parser = argparse.ArgumentParser(description="Refresh Appspace token via browser")
    parser.add_argument("--headed", action="store_true", help="Force visible browser")
    parser.add_argument("--headless", action="store_true", help="Force headless")
    parser.add_argument("--dry-run", action="store_true", help="Don't update GitHub secrets")
    args = parser.parse_args()

    headed = args.headed
    if args.headless:
        headed = False
    elif not args.headed and not STATE_FILE.exists():
        headed = True

    print("=" * 60)
    print("Appspace Token Refresher")
    print("=" * 60)

    tokens = capture_token(headed=headed)

    if not tokens:
        print("\nERROR: Could not capture token. Try running with --headed")
        sys.exit(1)

    session_token = tokens["session_token"]
    print(f"\nCaptured session token: {session_token[:20]}...{session_token[-10:]}")
    print(f"  Token length: {len(session_token)}")

    if tokens.get("refresh_token"):
        print(f"  Also captured refresh token")

    if args.dry_run:
        print("\n[DRY RUN] Would update GitHub secrets:")
        print(f"  APPSPACE_SESSION_TOKEN = {session_token[:20]}...")
        if tokens.get("refresh_token"):
            print(f"  APPSPACE_REFRESH_TOKEN = {tokens['refresh_token'][:20]}...")
        return

    print("\nUpdating GitHub Secrets...")
    if update_github_secret("APPSPACE_SESSION_TOKEN", session_token):
        print("  APPSPACE_SESSION_TOKEN updated")
    else:
        sys.exit(1)

    if tokens.get("refresh_token"):
        if update_github_secret("APPSPACE_REFRESH_TOKEN", tokens["refresh_token"]):
            print("  APPSPACE_REFRESH_TOKEN updated")

    print("\nDone! GitHub Actions should now use the fresh token.")


if __name__ == "__main__":
    main()
