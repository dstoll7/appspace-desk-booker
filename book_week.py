#!/usr/bin/env python3
"""Weekly desk-booking ritual — run locally, once a week.

WHY THIS EXISTS
---------------
Appspace refresh tokens die ~1 hour after an interactive Okta login. The API
advertises a 365-day lifetime (`refreshTokenExpiresIn: 31536000`) but does NOT
honor it — the real lifetime is tied to the parent Disney/Okta SSO session.
That makes fully-unattended cloud booking impossible: any GitHub Actions run
fires long after the token is dead and 401s.

So instead of pretending it runs hands-off, we lean into Appspace's 7-day
booking window: ONE local login lets us book every remaining weekday at once,
all inside the token's short lifetime.

WHAT IT DOES
------------
1. Opens a browser for the Disney/Okta login (you approve the push).
2. Mints a session token from the captured refresh token.
3. Books desk 08W-125-G for every Mon-Thu inside the next 7 days that you don't
   already hold.

Run it ~weekly (e.g. each Thursday/Friday for the coming week).

Usage:
    python book_week.py            # re-auth + book the week
    python book_week.py --list     # just print which days are in range (no login)
"""

import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import book_desk
from refresh_token import capture_refresh_token, mint_session_token

# Records dates we've already successfully booked, so the job can run on several
# mornings (resilience if the laptop is closed) without popping a browser when
# there's nothing new to do. Lives next to the script; gitignored.
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".book_week_state.json")


def load_booked_dates(today):
    """Return the set of YYYY-MM-DD strings we've already booked (today onward)."""
    try:
        with open(STATE_FILE) as f:
            dates = set(json.load(f).get("booked", []))
    except (OSError, ValueError):
        return set()
    # Drop anything in the past so the file doesn't grow forever.
    return {d for d in dates if d >= today.isoformat()}


def save_booked_dates(dates):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"booked": sorted(dates)}, f, indent=2)
    except OSError as e:
        print(f"⚠ Could not write state file ({e}) — not fatal.")


def target_weekdays(today):
    """Mon-Thu dates within the next 7 days (the bookable window), oldest first."""
    days = []
    for offset in range(1, 8):
        d = today + timedelta(days=offset)
        if book_desk.is_weekday(d):  # Mon-Thu only
            days.append(d)
    return days


def hours_until_start(booking_date, now, eastern):
    """Hours from now until the 9:30 AM ET start on booking_date."""
    start_hour, start_minute, _, _ = book_desk.get_booking_times()
    start = datetime(
        booking_date.year, booking_date.month, booking_date.day,
        start_hour, start_minute, 0, tzinfo=eastern,
    )
    return (start - now).total_seconds() / 3600


def main():
    list_only = "--list" in sys.argv
    force = "--force" in sys.argv

    eastern = ZoneInfo(book_desk.TIMEZONE)
    now = datetime.now(eastern)
    today = now.date()
    days = target_weekdays(today)

    # Only days actually inside the 168-hour booking window are bookable now.
    bookable = [d for d in days if hours_until_start(d, now, eastern) <= 168]

    print("=" * 60)
    print("Appspace Weekly Desk Booker")
    print("=" * 60)
    print(f"Today: {today.strftime('%A, %B %d, %Y')}")
    print(f"Desk:  {book_desk.DESK_NAME} (7 Hudson)")
    print("Target weekdays in the 7-day window:")
    for d in days:
        flag = "  (beyond 168h — not bookable yet)" if d not in bookable else ""
        print(f"  • {d.strftime('%A %b %d')}{flag}")

    if list_only:
        return

    # Idempotency guard: if every bookable day is already recorded as booked,
    # there's nothing to do — exit WITHOUT opening a browser. This lets launchd
    # fire the job on several mornings (in case the laptop is closed) while only
    # prompting for the Okta push when an actual booking is needed.
    already = load_booked_dates(today)
    if not force and bookable and all(d.isoformat() in already for d in bookable):
        print("\n✓ All bookable weekdays are already booked — nothing to do, skipping login.")
        return

    # 1. Re-auth (browser) and mint a session token.
    print("\n" + "=" * 60)
    refresh_token = capture_refresh_token()
    if not refresh_token:
        print("\nERROR: Could not capture a refresh token. Did the dashboard load?")
        sys.exit(1)
    print(f"✓ Captured refresh token: {refresh_token[:8]}...{refresh_token[-4:]}")

    session_token = mint_session_token(refresh_token)
    if not session_token:
        print("ERROR: refresh token failed to mint a session token — aborting.")
        sys.exit(1)
    print(f"✓ Session token minted: {session_token[:8]}...")
    tokens = {"session_token": session_token}

    # 2. Book each bookable weekday.
    booked, held, failed = [], [], []
    newly_confirmed = set(already)  # carry forward prior successes
    out_of_window = [d for d in days if d not in bookable]

    for d in bookable:
        label = d.strftime("%A %b %d")
        print(f"\n--- {label} ---")
        if book_desk.check_existing_reservations(tokens, booking_date=d):
            held.append(label)
            newly_confirmed.add(d.isoformat())
        elif book_desk.create_reservation(tokens, booking_date=d):
            booked.append(label)
            newly_confirmed.add(d.isoformat())
        else:
            failed.append(label)

    save_booked_dates(newly_confirmed)

    # 3. Summary.
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    if booked:
        print(f"✅ Booked:        {', '.join(booked)}")
    if held:
        print(f"✓  Already held:  {', '.join(held)}")
    if out_of_window:
        print(f"⏭️  Out of window: {', '.join(d.strftime('%A %b %d') for d in out_of_window)} "
              "(a later run will grab these)")
    if failed:
        print(f"❌ FAILED:        {', '.join(failed)}")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
