#!/usr/bin/env python3
"""Appspace Refresh-Token Seeder.

Opens a browser to Appspace, you complete the Disney/Okta login, and it captures
the REFRESH token from the auth response and stores it as the
APPSPACE_REFRESH_TOKEN GitHub Secret.

⚠️ The captured token dies ~1 HOUR after login (the API's `refreshTokenExpiresIn:
31536000` / 365-day claim is NOT honored — see README). So this only enables the
cloud `book-desk.yml` dispatch as a backup *within the hour*. For normal weekly
booking, use `book_week.py`, which re-auths and books the whole week locally in
one shot.

Usage:
    python refresh_token.py            # visible browser (default)
    python refresh_token.py --dry-run  # capture + validate, don't store secret
"""

import argparse
import base64
import json
import subprocess
import sys
import urllib.request

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from playwright.sync_api import Error as PlaywrightError


class TransientBrowserError(Exception):
    """Browser/network failure that is infrastructure noise, not a real problem.

    launchd fires the booker on wake, when Wi-Fi is often still re-negotiating —
    so the browser can fail to launch, or a navigation can die mid-flight with
    ERR_NETWORK_CHANGED / ERR_INTERNET_DISCONNECTED, even though a preflight
    curl succeeded moments earlier. These used to escape as uncaught Playwright
    exceptions (exit 1), which looked identical to a genuine failure and fired a
    "FAILED" notification every morning. They're retried, then reported as
    transient so the caller can stay silent.
    """


# Navigation errors worth retrying: the network changed underneath us rather
# than the site being broken.
_TRANSIENT_MARKERS = (
    "ERR_NETWORK_CHANGED",
    "ERR_INTERNET_DISCONNECTED",
    "ERR_NAME_NOT_RESOLVED",
    "ERR_CONNECTION_RESET",
    "ERR_CONNECTION_CLOSED",
    "ERR_CONNECTION_TIMED_OUT",
    "ERR_ADDRESS_UNREACHABLE",
    "ERR_NETWORK_IO_SUSPENDED",
)


def _is_transient(exc: Exception) -> bool:
    return any(marker in str(exc) for marker in _TRANSIENT_MARKERS)


APPSPACE_URL = "https://disney.cloud.appspace.com"
BASE_URL = f"{APPSPACE_URL}/api/v3"
GITHUB_REPO = "dstoll7/appspace-desk-booker"
USER_ID = "0b7f4f61-7d08-4d14-b748-10359ab2bcf5"
SUBJECT_TYPE = "UserStreaming"


def mint_session_token(refresh_token: str) -> str | None:
    """Exchange the refresh token for a session token (validates it works)."""
    payload = {
        "subjectId": USER_ID,
        "subjectType": SUBJECT_TYPE,
        "grantType": "refreshToken",
        "refreshToken": refresh_token,
    }
    req = urllib.request.Request(
        f"{BASE_URL}/authorization/token",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json;charset=UTF-8", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            access = json.loads(resp.read()).get("accessToken", "")
        claims = json.loads(base64.urlsafe_b64decode(
            access.split(".")[1] + "=" * (-len(access.split(".")[1]) % 4)))
        return claims.get("sourceId")
    except Exception:
        return None


def update_github_secret(name: str, value: str) -> bool:
    result = subprocess.run(
        ["gh", "secret", "set", name, "--repo", GITHUB_REPO, "--body", value],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  ERROR updating secret: {result.stderr}")
        return False
    return True


def capture_refresh_token() -> str | None:
    """Open a visible browser, complete SSO, capture the refresh token.

    Returns the token, or None if the Okta login wasn't completed in time.
    Raises TransientBrowserError if the browser/network failed in a way that's
    worth retrying later (common when launchd fires this on wake).
    """
    print("Launching browser (visible)...")
    captured = {}

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=False)
            except (PlaywrightTimeout, PlaywrightError) as e:
                # A launch timeout on wake means the machine was too busy or the
                # network stack wasn't ready — retryable, not a real failure.
                raise TransientBrowserError(f"browser failed to launch: {e}") from e

            try:
                context = browser.new_context(viewport={"width": 1280, "height": 800})
                page = context.new_page()

                def handle_response(response):
                    if "/api/v3/authorization/token" in response.url and response.status == 200:
                        try:
                            body = response.json()
                            if body.get("refreshToken"):
                                captured["refresh_token"] = body["refreshToken"]
                        except Exception:
                            pass

                page.on("response", handle_response)

                # Retry the initial navigation: on wake, Wi-Fi often re-negotiates
                # mid-flight and kills it with ERR_NETWORK_CHANGED even though a
                # preflight curl just succeeded.
                print(f"Navigating to {APPSPACE_URL}...")
                attempts = 3
                for attempt in range(1, attempts + 1):
                    try:
                        page.goto(APPSPACE_URL, wait_until="domcontentloaded")
                        break
                    except (PlaywrightTimeout, PlaywrightError) as e:
                        if attempt == attempts or not _is_transient(e):
                            raise TransientBrowserError(
                                f"navigation to {APPSPACE_URL} failed after "
                                f"{attempt} attempt(s): {e}"
                            ) from e
                        backoff = 10 * attempt
                        print(f"  network changed mid-navigation (attempt {attempt}/{attempts}); "
                              f"retrying in {backoff}s...")
                        page.wait_for_timeout(backoff * 1000)

                print("\n" + "=" * 60)
                print("Complete the Disney/Okta login in the browser window.")
                print("=" * 60 + "\n")

                try:
                    page.wait_for_url("**/disney.cloud.appspace.com/console/**", timeout=180_000)
                except PlaywrightTimeout:
                    pass

                # Let the post-login auth call settle so we capture the refresh token
                try:
                    page.wait_for_timeout(6000)
                except Exception:
                    pass
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
    except TransientBrowserError:
        raise
    except (PlaywrightTimeout, PlaywrightError) as e:
        # Anything else Playwright threw while the network was unsettled.
        raise TransientBrowserError(f"browser session failed: {e}") from e

    return captured.get("refresh_token")


def main():
    parser = argparse.ArgumentParser(description="Seed the Appspace refresh token")
    parser.add_argument("--dry-run", action="store_true", help="Don't store the secret")
    args = parser.parse_args()

    print("=" * 60)
    print("Appspace Refresh-Token Seeder")
    print("=" * 60)

    refresh_token = capture_refresh_token()
    if not refresh_token:
        print("\nERROR: Could not capture a refresh token.")
        print("  Make sure you completed the login and the dashboard loaded.")
        sys.exit(1)

    print(f"\n✓ Captured refresh token: {refresh_token[:8]}...{refresh_token[-4:]}")

    print("\nValidating (minting a session token from it)...")
    session_token = mint_session_token(refresh_token)
    if not session_token:
        print("  ERROR: refresh token failed to mint a session token — aborting.")
        sys.exit(1)
    print(f"  ✓ Minted session token: {session_token[:8]}... (refresh token works)")

    if args.dry_run:
        print("\n[DRY RUN] Would store APPSPACE_REFRESH_TOKEN")
        return

    print("\nStoring APPSPACE_REFRESH_TOKEN secret...")
    if not update_github_secret("APPSPACE_REFRESH_TOKEN", refresh_token):
        sys.exit(1)
    print("  ✓ APPSPACE_REFRESH_TOKEN updated")
    print("\n⚠️  This token dies in ~1 hour. Dispatch book-desk.yml now if you need it,"
          "\n    or just use `python book_week.py` to book locally.")


if __name__ == "__main__":
    main()
