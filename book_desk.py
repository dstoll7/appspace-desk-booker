#!/usr/bin/env python3
"""
Appspace Desk Auto-Booker (GitHub Actions version)
Automatically books desk 08W-125-G at 7 Hudson, 7 days in advance.

Environment Variables (one of):
  - APPSPACE_REFRESH_TOKEN: Long-lived (365-day) refresh token. Preferred —
    a fresh session token is minted from it on every run via plain HTTP, so the
    whole thing runs unattended in CI with no browser/Okta/manual login.
  - APPSPACE_SESSION_TOKEN: A pre-minted session token (fallback for local runs).
"""

import base64
import json
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests

# =============================================================================
# CONFIGURATION
# =============================================================================

BASE_URL = "https://disney.cloud.appspace.com/api/v3"
TIMEZONE = "America/New_York"


class TokenExpiredError(Exception):
    """Raised when the Appspace API rejects the token (401).

    This exists so a dead token surfaces as an explicit auth failure instead of
    being silently swallowed and misreported as "no reservation found".
    """

# User details
USER_ID = "0b7f4f61-7d08-4d14-b748-10359ab2bcf5"
USER_NAME = "Daniel Stoll"
USER_EMAIL = "Daniel.Stoll@disney.com"

# Desk details
DESK_RESOURCE_ID = "3a1b388a-08ec-4e16-acde-cebd64ebc86d"
DESK_NAME = "08W-125-G"

# Booking time (local Eastern time) - defaults, can be overridden with --start-time and --end-time
DEFAULT_START_HOUR = 9
DEFAULT_START_MINUTE = 30
DEFAULT_END_HOUR = 17
DEFAULT_END_MINUTE = 30


def get_booking_times():
    """Get booking times from command line or use defaults."""
    start_hour, start_minute = DEFAULT_START_HOUR, DEFAULT_START_MINUTE
    end_hour, end_minute = DEFAULT_END_HOUR, DEFAULT_END_MINUTE
    
    for i, arg in enumerate(sys.argv):
        if arg == "--start-time" and i + 1 < len(sys.argv):
            try:
                parts = sys.argv[i + 1].split(":")
                start_hour = int(parts[0])
                start_minute = int(parts[1]) if len(parts) > 1 else 0
            except (ValueError, IndexError):
                pass
        elif arg == "--end-time" and i + 1 < len(sys.argv):
            try:
                parts = sys.argv[i + 1].split(":")
                end_hour = int(parts[0])
                end_minute = int(parts[1]) if len(parts) > 1 else 0
            except (ValueError, IndexError):
                pass
    
    return start_hour, start_minute, end_hour, end_minute

# Days in advance to book (default, can be overridden with --days-ahead N)
DAYS_AHEAD = 7


def get_days_ahead():
    """Get days ahead from command line or use default."""
    for i, arg in enumerate(sys.argv):
        if arg == "--days-ahead" and i + 1 < len(sys.argv):
            try:
                return int(sys.argv[i + 1])
            except ValueError:
                pass
    return DAYS_AHEAD


def should_force():
    """Check if --force flag is set to skip existing reservation check."""
    return "--force" in sys.argv

# =============================================================================
# TOKEN MANAGEMENT
# =============================================================================

SUBJECT_TYPE = "UserStreaming"


def mint_session_token(refresh_token):
    """Exchange the long-lived refresh token for a fresh session token.

    Pure HTTP — no browser, no Okta, no manual login. The Appspace API uses the
    session token (the JWT's `sourceId` claim) as the `token:` header value.
    The refresh token is non-rotating and valid for ~365 days.
    """
    payload = {
        "subjectId": USER_ID,
        "subjectType": SUBJECT_TYPE,
        "grantType": "refreshToken",
        "refreshToken": refresh_token,
    }
    resp = requests.post(
        f"{BASE_URL}/authorization/token",
        headers={
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json, text/plain, */*",
        },
        json=payload,
        timeout=30,
    )
    if resp.status_code != 200:
        raise TokenExpiredError(
            f"Refresh-token grant failed (HTTP {resp.status_code}). "
            f"The refresh token may be expired or revoked — run refresh_token.py "
            f"locally to mint a new one."
        )

    access_token = resp.json().get("accessToken", "")
    parts = access_token.split(".")
    if len(parts) < 2:
        raise TokenExpiredError("Refresh grant returned no usable accessToken JWT")
    claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    session_token = claims.get("sourceId")
    if not session_token:
        raise TokenExpiredError("Could not extract session token (sourceId) from JWT")
    return session_token


def get_tokens():
    """Get a session token — minted fresh from the refresh token when available."""
    refresh_token = os.environ.get("APPSPACE_REFRESH_TOKEN")
    if refresh_token:
        print("🔑 Minting fresh session token from refresh token (no browser)...")
        session_token = mint_session_token(refresh_token)
        print(f"   ✓ Session token minted: {session_token[:8]}...{session_token[-4:]}")
        return {"session_token": session_token}

    # Fallback: a pre-minted session token (e.g. local one-off runs)
    session_token = os.environ.get("APPSPACE_SESSION_TOKEN")
    if not session_token:
        print("ERROR: set APPSPACE_REFRESH_TOKEN (preferred) or APPSPACE_SESSION_TOKEN")
        sys.exit(1)
    print("⚠ Using pre-set APPSPACE_SESSION_TOKEN (no refresh token available)")
    return {"session_token": session_token}


# =============================================================================
# BOOKING FUNCTIONS
# =============================================================================

def get_booking_date(days_ahead=None):
    """Calculate the date to book (N days from now in Eastern time).

    Appspace enforces a 168-hour (exactly 7 days) booking window.
    If the requested booking would exceed that, reduce days_ahead by 1.
    """
    eastern = ZoneInfo(TIMEZONE)
    now = datetime.now(eastern)
    if days_ahead is None:
        days_ahead = get_days_ahead()

    start_hour, start_minute, _, _ = get_booking_times()
    candidate_date = (now + timedelta(days=days_ahead)).date()
    candidate_start = datetime(
        candidate_date.year, candidate_date.month, candidate_date.day,
        start_hour, start_minute, 0, tzinfo=eastern,
    )

    # If booking start is more than 168 hours away, reduce by 1 day
    hours_until = (candidate_start - now).total_seconds() / 3600
    if hours_until > 168:
        days_ahead -= 1
        candidate_date = (now + timedelta(days=days_ahead)).date()

    return candidate_date


def is_weekday(date):
    """Check if the date is Mon-Thu."""
    return date.weekday() < 4  # 0=Mon, 3=Thu


def create_reservation(tokens):
    """Create a desk reservation for N days from now."""
    eastern = ZoneInfo(TIMEZONE)
    utc = ZoneInfo("UTC")
    
    # Calculate booking datetime
    booking_date = get_booking_date()
    
    # Skip Fri-Sun
    if not is_weekday(booking_date):
        print(f"⏭️  Skipping {booking_date.strftime('%A')} - not a Mon-Thu booking day")
        return True  # Return True so we don't fail the workflow
    
    # Get booking times (may be overridden via command line)
    start_hour, start_minute, end_hour, end_minute = get_booking_times()
    
    # Create start and end times in Eastern, then convert to UTC
    start_local = datetime(
        booking_date.year, booking_date.month, booking_date.day,
        start_hour, start_minute, 0,
        tzinfo=eastern
    )
    end_local = datetime(
        booking_date.year, booking_date.month, booking_date.day,
        end_hour, end_minute, 0,
        tzinfo=eastern
    )
    
    # Convert to UTC for API
    start_utc = start_local.astimezone(utc)
    end_utc = end_local.astimezone(utc)
    
    # Format for API (ISO 8601 with milliseconds)
    start_str = start_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    end_str = end_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    
    # Format display time
    start_display = start_local.strftime("%I:%M %p").lstrip("0")
    end_display = end_local.strftime("%I:%M %p").lstrip("0")
    
    print(f"\n📅 Booking desk {DESK_NAME} for {booking_date.strftime('%A, %B %d, %Y')}")
    print(f"   Time: {start_display} - {end_display} ET")
    print(f"   UTC: {start_str} - {end_str}")
    
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/json, text/plain, */*",
        "token": tokens["session_token"],
        "x-appspace-request-timezone": TIMEZONE,
    }
    
    payload = {
        "resourceIds": [DESK_RESOURCE_ID],
        "effectiveStartAt": start_str,
        "effectiveEndAt": end_str,
        "organizer": {
            "id": USER_ID,
            "name": USER_NAME,
        },
        "sensitivity": "Public",
        "organizerAvailabilityType": "Busy",
        "attendees": [{
            "displayName": USER_NAME,
            "email": USER_EMAIL,
            "resourceIds": [DESK_RESOURCE_ID],
            "attendanceType": "InPerson",
            "userId": USER_ID,
            "id": USER_ID,
        }],
        "visitors": [],
        "visitPurpose": "",
        "isAllDay": False,
        "startTimeZone": TIMEZONE,
        "endTimeZone": TIMEZONE,
    }
    
    # First, try to lock the resource
    lock_payload = {
        "resourceIds": [DESK_RESOURCE_ID],
        "from": start_str,
        "to": end_str,
    }
    
    try:
        lock_response = requests.post(
            f"{BASE_URL}/reservation/locks/resources",
            headers=headers,
            json=lock_payload,
            timeout=30,
        )
        
        if lock_response.status_code == 204:
            print("   ✓ Resource locked")
        else:
            print(f"   ⚠ Lock failed (continuing): {lock_response.status_code}")
    except Exception as e:
        print(f"   ⚠ Lock request failed: {e}")
    
    # Create the reservation
    try:
        response = requests.post(
            f"{BASE_URL}/reservation/reservations",
            headers=headers,
            json=payload,
            timeout=30,
        )
        
        if response.status_code == 201:
            data = response.json()
            reservation_id = data.get("id")
            status = data.get("status")
            print(f"\n✅ SUCCESS! Reservation created")
            print(f"   Reservation ID: {reservation_id}")
            print(f"   Status: {status}")
            return True
        elif response.status_code == 409:
            print(f"\n⚠️  CONFLICT: Desk already booked for this time slot")
            try:
                error_data = response.json()
                print(f"   Details: {error_data.get('message', 'No details')}")
            except:
                print(f"   Response: {response.text}")
            
            # IMPORTANT: 409 doesn't mean YOU have the desk - someone else might!
            # Verify by checking our actual reservations
            print(f"\n🔍 Verifying if YOU have the reservation...")
            if check_existing_reservations(tokens):
                print(f"✅ Confirmed: You already have {DESK_NAME} reserved")
                return True
            else:
                print(f"❌ FAILED: {DESK_NAME} was booked by someone else!")
                print(f"   Consider running the booking earlier (before midnight)")
                return False
        elif response.status_code == 401:
            print(f"\n❌ UNAUTHORIZED: Token may have expired")
            print("   Please update APPSPACE_SESSION_TOKEN in GitHub Secrets")
            return False
        else:
            print(f"\n❌ FAILED: {response.status_code}")
            print(response.text)
            return False
    except Exception as e:
        print(f"\n❌ Request failed: {e}")
        return False


def check_existing_reservations(tokens):
    """Check if there's already a reservation for the target date."""
    eastern = ZoneInfo(TIMEZONE)
    utc = ZoneInfo("UTC")
    
    booking_date = get_booking_date()
    
    # Skip weekend check
    if not is_weekday(booking_date):
        return False
    
    # Create date range for the target day
    start_of_day = datetime(
        booking_date.year, booking_date.month, booking_date.day,
        0, 0, 0, tzinfo=eastern
    ).astimezone(utc)
    
    end_of_day = datetime(
        booking_date.year, booking_date.month, booking_date.day,
        23, 59, 59, tzinfo=eastern
    ).astimezone(utc)
    
    headers = {
        "Accept": "application/json, text/plain, */*",
        "token": tokens["session_token"],
        "x-appspace-request-timezone": TIMEZONE,
    }
    
    params = {
        "sort": "startAt",
        "status": "NotConfirmed, Pending, Checkin, Active, Conflict, Completed",
        "includesourceobject": "true",
        "startAt": start_of_day.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "endAt": end_of_day.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "page": 1,
        "start": 0,
        "limit": 20,
        "pagecount": 20,
    }
    
    try:
        response = requests.get(
            f"{BASE_URL}/reservation/users/me/events",
            headers=headers,
            params=params,
            timeout=30,
        )
        
        if response.status_code == 401:
            raise TokenExpiredError("401 from /reservation/users/me/events")

        if response.status_code == 200:
            data = response.json()
            items = data.get("items", [])

            for item in items:
                # Check resources at top level
                resources = item.get("resources", [])
                for resource in resources:
                    if resource.get("id") == DESK_RESOURCE_ID:
                        print(f"⚠️  Already have a reservation for {DESK_NAME} on {booking_date}")
                        return True

                # Also check nested reservation.resources (API returns both formats)
                reservation = item.get("reservation", {})
                res_resources = reservation.get("resources", [])
                for resource in res_resources:
                    if resource.get("id") == DESK_RESOURCE_ID:
                        print(f"⚠️  Already have a reservation for {DESK_NAME} on {booking_date}")
                        return True
    except TokenExpiredError:
        raise
    except Exception as e:
        print(f"⚠ Could not check existing reservations: {e}")

    return False


# =============================================================================
# CHECK-IN FUNCTIONS
# =============================================================================

def get_todays_events(tokens):
    """Get today's reservations to find the event ID for check-in."""
    eastern = ZoneInfo(TIMEZONE)
    utc = ZoneInfo("UTC")
    
    today = datetime.now(eastern).date()
    
    # Create date range for today
    start_of_day = datetime(
        today.year, today.month, today.day,
        0, 0, 0, tzinfo=eastern
    ).astimezone(utc)
    
    end_of_day = datetime(
        today.year, today.month, today.day,
        23, 59, 59, tzinfo=eastern
    ).astimezone(utc)
    
    headers = {
        "Accept": "application/json, text/plain, */*",
        "token": tokens["session_token"],
        "x-appspace-request-timezone": TIMEZONE,
    }
    
    # Don't filter by status - we want to find all reservations and check their status
    params = {
        "sort": "startAt",
        "includesourceobject": "true",
        "startAt": start_of_day.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "endAt": end_of_day.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "limit": 20,
    }
    
    try:
        response = requests.get(
            f"{BASE_URL}/reservation/users/me/events",
            headers=headers,
            params=params,
            timeout=30,
        )
        
        if response.status_code == 401:
            raise TokenExpiredError("401 from /reservation/users/me/events")

        if response.status_code == 200:
            data = response.json()
            return data.get("items", [])
    except TokenExpiredError:
        raise
    except Exception as e:
        print(f"⚠ Could not get today's events: {e}")

    return []


def wait_for_checkin_window():
    """Sleep until the check-in window opens — WITHOUT needing a token.

    Appspace's check-in window is 15 min before to 15 min after the booked start
    time. The check-in workflow is scheduled early (8 AM ET) to absorb GitHub's
    cron delays, so this runs first to burn the slack, and the fresh token is
    minted only AFTER it returns — guaranteeing the token is seconds old when we
    actually check in (it has a ~90-min TTL, so minting before this wait would
    let it die mid-sleep).
    """
    eastern = ZoneInfo(TIMEZONE)
    start_hour, start_minute, _, _ = get_booking_times()

    now = datetime.now(eastern)
    today = now.date()
    start_dt = datetime(
        today.year, today.month, today.day,
        start_hour, start_minute, 0, tzinfo=eastern,
    )
    window_start = start_dt - timedelta(minutes=15)
    window_end = start_dt + timedelta(minutes=15)

    print(f"   Booked start: {start_dt.strftime('%I:%M %p ET')}")
    print(f"   Check-in window: {window_start.strftime('%I:%M %p')} - {window_end.strftime('%I:%M %p ET')}")
    print(f"   Current time: {now.strftime('%I:%M %p ET')}")

    if now < window_start:
        # Wake up ~30s before the window opens so the token mint that follows
        # lands us right at window open with a brand-new token.
        wait_seconds = max(0, (window_start - now).total_seconds() - 30)
        print(f"\n⏳ Window opens in {int((window_start - now).total_seconds() / 60)} min — "
              f"sleeping {int(wait_seconds)}s, then minting a fresh token")
        time.sleep(wait_seconds)
        print(f"   Resumed at {datetime.now(eastern).strftime('%I:%M %p ET')}")
    elif now > window_end:
        late_min = int((now - window_end).total_seconds() / 60)
        print(f"\n⚠️  Cron fired {late_min} min after the window closed — "
              f"proceeding anyway (check-in will likely be rejected by Appspace)")
    else:
        print("\n✓ Already inside the check-in window — proceeding to mint token")


def checkin_reservation(tokens):
    """Check in to today's desk reservation."""
    eastern = ZoneInfo(TIMEZONE)
    
    print("\n🔍 Looking for today's reservation to check in...")
    
    events = get_todays_events(tokens)
    
    if not events:
        print("   No reservations found for today")
        return False
    
    # Find the reservation for our desk
    # API returns resources in two formats: top-level event["resources"] and nested event["reservation"]["resources"]
    target_event = None
    for event in events:
        # Check top-level resources
        for resource in event.get("resources", []):
            if resource.get("id") == DESK_RESOURCE_ID:
                target_event = event
                break
        # Also check nested reservation.resources
        if not target_event:
            reservation = event.get("reservation", {})
            for resource in reservation.get("resources", []):
                if resource.get("id") == DESK_RESOURCE_ID:
                    target_event = event
                    break
        if target_event:
            break
    
    if not target_event:
        print(f"   No reservation found for desk {DESK_NAME}")
        return False
    
    event_id = target_event.get("id")
    reservation = target_event.get("reservation", {})
    # API returns status in either the nested reservation object or at the top level
    event_status = reservation.get("status") or target_event.get("status", "Unknown")
    start_at = target_event.get("startAt", "") or reservation.get("effectiveStartAt", "")
    
    print(f"\n📋 Found reservation:")
    print(f"   Event ID: {event_id}")
    print(f"   Status: {event_status}")
    print(f"   Start: {start_at}")
    
    # Check if already checked in
    if event_status == "Active":
        print("\n✅ Already checked in!")
        return True
    
    # Parse start time and check if we're in the check-in window
    # Appspace window is exactly 15 minutes before to 15 minutes after start time
    if start_at:
        try:
            start_dt = datetime.fromisoformat(start_at.replace("Z", "+00:00")).astimezone(eastern)
            now = datetime.now(eastern)
            window_start = start_dt - timedelta(minutes=15)
            window_end = start_dt + timedelta(minutes=15)
            
            print(f"   Check-in window: {window_start.strftime('%I:%M %p')} - {window_end.strftime('%I:%M %p ET')}")
            print(f"   Current time: {now.strftime('%I:%M %p ET')}")
            
            if now < window_start:
                seconds_until = (window_start - now).total_seconds()
                minutes_until = int(seconds_until / 60)
                
                # Sleep until window opens (handles GitHub Actions cron timing variability)
                wait_seconds = seconds_until + 30  # 30 second buffer
                print(f"\n⏳ Check-in window opens in {minutes_until} minutes")
                print(f"   Waiting {int(wait_seconds)} seconds until window opens...")
                time.sleep(wait_seconds)
                # Update 'now' after sleeping
                now = datetime.now(eastern)
                print(f"   Resumed at {now.strftime('%I:%M %p ET')} - proceeding with check-in")
            elif now > window_end:
                print(f"\n⚠️  Check-in window closed {int((now - window_end).total_seconds() / 60)} minutes ago")
                print("   Will attempt check-in anyway...")
        except Exception as e:
            print(f"   ⚠️  Could not parse start time: {e}")
    
    # Valid statuses for check-in attempt
    valid_statuses = ["Confirmed", "Checkin", "Pending", "NotConfirmed"]
    if event_status not in valid_statuses:
        print(f"\n⚠️  Cannot check in - status is {event_status}")
        print(f"   Expected one of: {valid_statuses}")
        return False
    
    print(f"\n🔄 Attempting check-in (status: {event_status})...")
    
    # Perform check-in
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/json, text/plain, */*",
        "token": tokens["session_token"],
        "x-appspace-request-timezone": TIMEZONE,
    }
    
    payload = {
        "resourceIds": [DESK_RESOURCE_ID]
    }
    
    try:
        response = requests.post(
            f"{BASE_URL}/reservation/events/{event_id}/checkin",
            headers=headers,
            json=payload,
            timeout=30,
        )
        
        if response.status_code == 202:
            print(f"\n✅ CHECK-IN SUCCESSFUL!")
            print(f"   Desk {DESK_NAME} confirmed for today")
            return True
        else:
            print(f"\n❌ Check-in failed: {response.status_code}")
            print(response.text)
            return False
    except Exception as e:
        print(f"\n❌ Check-in request failed: {e}")
        return False


# =============================================================================
# MAIN
# =============================================================================

def main():
    """Main entry point."""
    eastern = ZoneInfo(TIMEZONE)

    # Check for flags
    do_checkin = "--checkin" in sys.argv
    do_wait = "--wait-for-checkin-window" in sys.argv
    do_healthcheck = "--healthcheck" in sys.argv

    # Token-free mode: just sleep until the check-in window opens, then exit.
    # The workflow mints the fresh token AFTER this returns.
    if do_wait:
        print("=" * 60)
        print("⏳ Waiting for check-in window (no token needed)")
        print(f"   {datetime.now(eastern).strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print("=" * 60)
        wait_for_checkin_window()
        return

    # Health-check mode: mint a session token from the refresh token and validate
    # it against the API. Exit 0 if healthy, exit 2 if the refresh token is dead.
    if do_healthcheck:
        print("=" * 60)
        print("🩺 Appspace refresh-token health check")
        print(f"   {datetime.now(eastern).strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print("=" * 60)
        try:
            tokens = get_tokens()
            resp = requests.get(
                f"{BASE_URL}/users/me",
                headers={"Accept": "application/json", "token": tokens["session_token"]},
                timeout=15,
            )
            if resp.status_code != 200:
                raise TokenExpiredError(f"/users/me returned HTTP {resp.status_code}")
            print("\n✅ HEALTHY: refresh token works — session token minted and validated.")
            return
        except TokenExpiredError as e:
            print(f"\n❌ AUTH FAILURE: {e}")
            print("   The refresh token is expired/revoked — run refresh_token.py locally.")
            sys.exit(2)

    print("=" * 60)
    if do_checkin:
        print("🏢 Appspace Desk Check-In (GitHub Actions)")
    else:
        print("🏢 Appspace Desk Auto-Booker (GitHub Actions)")
    print(f"   {datetime.now(eastern).strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print("=" * 60)

    try:
        # Mint / load the session token (raises TokenExpiredError if the
        # refresh token is dead).
        tokens = get_tokens()
        print("\n✓ Session token ready")

        if do_checkin:
            # Check-in mode
            result = checkin_reservation(tokens)
            if result:
                print(f"\n🎉 Checked in successfully!")
            else:
                print(f"\n😞 Check-in failed")
                sys.exit(1)
        else:
            # Booking mode
            # Check for existing reservation (unless --force is set)
            if should_force():
                print("\n⚡ Force mode - skipping existing reservation check")
            else:
                print("\n🔍 Checking for existing reservations...")
                if check_existing_reservations(tokens):
                    print("   Skipping - reservation already exists")
                    print("   Use --force to attempt booking anyway")
                    return
                print("   No existing reservation found")

            # Create the reservation
            success = create_reservation(tokens)

            if success:
                print(f"\n🎉 Done!")
            else:
                print(f"\n😞 Failed to book desk {DESK_NAME}")
                sys.exit(1)
    except TokenExpiredError as e:
        # Auth failed — make it loud and unambiguous instead of letting it
        # masquerade as "no reservation found".
        print(f"\n❌ AUTH FAILURE: {e}")
        print("   The refresh token is likely expired/revoked (it lasts ~365 days).")
        print("   Fix: run `python refresh_token.py` locally once to mint a new one.")
        sys.exit(2)


if __name__ == "__main__":
    main()

