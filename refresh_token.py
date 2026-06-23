#!/usr/bin/env python3
"""
Appspace Token Refresher using Playwright.

Opens a browser to Appspace, captures the session token from cookies/headers,
and updates the GitHub Secret.

Usage:
    python refresh_token.py              # Headed browser (default)
    python refresh_token.py --headless   # Headless (only works if SSO session is active)
    python refresh_token.py --ci         # CI mode: load Okta state from env, run headless
    python refresh_token.py --dry-run    # Don't update GitHub secrets
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

APPSPACE_URL = "https://disney.cloud.appspace.com"
STATE_DIR = Path(__file__).parent / ".playwright-state"
STATE_FILE = STATE_DIR / "auth-state.json"
GITHUB_REPO = "dstoll7/appspace-desk-booker"


def validate_token(token: str) -> bool:
    """Confirm the token works against the Appspace API before saving it."""
    req = urllib.request.Request(
        f"{APPSPACE_URL}/api/v3/users/me",
        headers={"Accept": "application/json", "token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False


def update_github_secret(name: str, value: str) -> bool:
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


def load_state_from_env() -> bool:
    """Load Playwright auth state from PLAYWRIGHT_AUTH_STATE env var (base64 JSON)."""
    state_b64 = os.environ.get("PLAYWRIGHT_AUTH_STATE")
    if not state_b64:
        return False
    try:
        STATE_DIR.mkdir(exist_ok=True)
        state_json = base64.b64decode(state_b64).decode("utf-8")
        STATE_FILE.write_text(state_json)
        print("Loaded Okta/Appspace auth state from environment")
        return True
    except Exception as e:
        print(f"WARNING: Could not load auth state from env: {e}")
        return False


def save_state_to_secret() -> bool:
    """Encode current Playwright state and store it as PLAYWRIGHT_AUTH_STATE secret."""
    if not STATE_FILE.exists():
        return False
    try:
        state_b64 = base64.b64encode(STATE_FILE.read_bytes()).decode()
        if update_github_secret("PLAYWRIGHT_AUTH_STATE", state_b64):
            print("  ✓ PLAYWRIGHT_AUTH_STATE updated (seeds future headless CI re-auth)")
            return True
    except Exception as e:
        print(f"  WARNING: Could not save auth state to secret: {e}")
    return False


def capture_token(headed: bool = True) -> dict | None:
    """Launch browser, navigate to Appspace, and capture the session token."""
    STATE_DIR.mkdir(exist_ok=True)

    has_state = STATE_FILE.exists()
    headless = not headed

    # When headed, clear saved state to force fresh SSO login
    if headed and has_state:
        STATE_FILE.unlink()
        has_state = False
        print("Cleared saved browser state to force fresh login")

    print(f"Launching browser ({'headless' if headless else 'visible'})...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context_opts = {"viewport": {"width": 1280, "height": 800}}
        if has_state:
            # Strip any existing Appspace session cookies from the stored state.
            # Without this, the headless browser reuses the old (nearly-expired)
            # session cookie and Appspace never issues a fresh one.
            # Okta/Disney SSO cookies are preserved so SSO still completes headlessly.
            try:
                state = json.loads(STATE_FILE.read_text())
                APPSPACE_COOKIE_NAMES = {"appspace-session-token", "appspace-core-token"}
                state["cookies"] = [
                    c for c in state.get("cookies", [])
                    if c.get("name") not in APPSPACE_COOKIE_NAMES
                ]
                stripped_state_file = STATE_DIR / "auth-state-stripped.json"
                stripped_state_file.write_text(json.dumps(state))
                context_opts["storage_state"] = str(stripped_state_file)
                print("Stripped old Appspace session cookies — will capture fresh token")
            except Exception as e:
                print(f"WARNING: Could not strip state cookies: {e} — using full state")
                context_opts["storage_state"] = str(STATE_FILE)

        context = browser.new_context(**context_opts)
        page = context.new_page()

        captured_token = {}

        def handle_response(response):
            """Intercept API responses to find tokens."""
            url = response.url
            if "disney.cloud.appspace.com/api/" in url and response.status == 200:
                request = response.request
                token = request.headers.get("token")
                if token and len(token) > 20:
                    captured_token["session_token"] = token

        page.on("response", handle_response)

        print(f"Navigating to {APPSPACE_URL}...")
        page.goto(APPSPACE_URL, wait_until="domcontentloaded")

        if not headless:
            print("\n" + "=" * 60)
            print("Please complete the Disney SSO login in the browser window.")
            print("The script will automatically detect when you're logged in.")
            print("=" * 60 + "\n")

        # Wait until we land on Appspace (post-login)
        try:
            page.wait_for_url("**/disney.cloud.appspace.com/**", timeout=120_000)
        except PlaywrightTimeout:
            pass

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

        print("Logged in! Capturing token...")

        # Wait for post-login API calls to settle (token is set in cookies/headers)
        time.sleep(5)
        try:
            page.wait_for_load_state("networkidle", timeout=20_000)
        except PlaywrightTimeout:
            # networkidle timed out — page has lingering activity but we likely
            # already captured the token from response headers or cookies.
            print("  (networkidle timeout — continuing with token capture)")
        except Exception:
            pass

        # Get session token from cookies (primary source)
        try:
            cookies = context.cookies()
            for cookie in cookies:
                if cookie["name"] == "appspace-session-token":
                    captured_token["session_token"] = cookie["value"]
                elif cookie["name"] == "appspace-core-token":
                    captured_token["core_token"] = cookie["value"]
        except Exception:
            pass

        # Save browser state (Okta + Appspace cookies) for future headless re-auth
        context.storage_state(path=str(STATE_FILE))
        browser.close()

    return captured_token if captured_token.get("session_token") else None


def main():
    parser = argparse.ArgumentParser(description="Refresh Appspace token via browser")
    parser.add_argument("--headed", action="store_true", default=True,
                        help="Force visible browser (default)")
    parser.add_argument("--headless", action="store_true", help="Force headless")
    parser.add_argument("--ci", action="store_true",
                        help="CI mode: load Okta state from PLAYWRIGHT_AUTH_STATE env var, run headless")
    parser.add_argument("--dry-run", action="store_true", help="Don't update GitHub secrets")
    args = parser.parse_args()

    # CI mode: headless + load Okta browser state from env var
    if args.ci:
        if not load_state_from_env():
            print("ERROR: --ci requires PLAYWRIGHT_AUTH_STATE secret to be set.")
            print("  Run refresh_token.py locally first to seed this secret.")
            sys.exit(1)
        headed = False
    else:
        headed = not args.headless

    print("=" * 60)
    print("Appspace Token Refresher")
    print("=" * 60)

    tokens = capture_token(headed=headed)

    if not tokens:
        print("\nERROR: Could not capture token.")
        if args.ci:
            print("  Okta session has likely expired — manual refresh required.")
        else:
            print("  Try running with --headed")
        sys.exit(1)

    session_token = tokens["session_token"]
    print(f"\n✓ Captured session token: {session_token[:10]}...{session_token[-6:]}")
    print(f"  Token length: {len(session_token)}")

    print("\nValidating token against Appspace API...")
    if not validate_token(session_token):
        print("  ERROR: Captured token failed API validation — aborting secret update.")
        print("  The browser may not have fully completed the SSO flow.")
        print("  Try running again and wait until the Appspace dashboard fully loads.")
        sys.exit(1)
    print("  ✓ Token validated successfully")

    if args.dry_run:
        print("\n[DRY RUN] Would update APPSPACE_SESSION_TOKEN and PLAYWRIGHT_AUTH_STATE")
        return

    # In a GitHub Actions job, export the fresh token to the job environment so the
    # very next step (book / check-in) uses this seconds-old token directly —
    # no round-trip through the secret, which wouldn't be re-injected mid-job anyway.
    github_env = os.environ.get("GITHUB_ENV")
    if github_env:
        try:
            with open(github_env, "a") as fh:
                fh.write(f"APPSPACE_SESSION_TOKEN={session_token}\n")
            print("  ✓ Exported APPSPACE_SESSION_TOKEN to job environment (GITHUB_ENV)")
        except Exception as e:
            print(f"  WARNING: Could not write to GITHUB_ENV: {e}")

    print("\nUpdating GitHub Secrets...")
    if update_github_secret("APPSPACE_SESSION_TOKEN", session_token):
        print("  ✓ APPSPACE_SESSION_TOKEN updated")
    else:
        sys.exit(1)

    # Always save Playwright browser state so CI can attempt headless re-auth next time
    save_state_to_secret()

    print("\nDone! GitHub Actions should now use the fresh token.")


if __name__ == "__main__":
    main()
