# Appspace Desk Booker 🏢

Books desk **08W-125-G** at 7 Hudson for the upcoming week in a single local run.

> **Not unattended.** An earlier version of this project tried to run hands-off in
> GitHub Actions for ~a year. That doesn't work — see [Why it can't be automated](#why-it-cant-be-automated).
> Booking is now a quick **weekly local ritual**.

## Configuration

| Setting | Value |
|---------|-------|
| Desk | 08W-125-G (8th floor, 7 Hudson) |
| Time | 9:30 AM - 5:30 PM Eastern |
| Booking Window | 7 days in advance (Appspace hard limit) |
| Days booked | Mon–Thu |

## Usage — book the week

Run this once a week (e.g. each Thursday/Friday for the coming week):

```
python book_week.py
```

It opens a browser for the Disney/Okta login (approve the push), then books every
Mon–Thu inside the next 7 days that you don't already hold. Days still beyond the
168-hour window are reported so you know to grab them on the next run.

```
python book_week.py --list   # preview which days are in range (no login)
```

## Why it can't be automated

Appspace session tokens can **only** be minted interactively through Disney's Okta
SSO — there is no headless/automated browser path (Okta blocks it, even with a warm
profile). The intended workaround was the **refresh token**: capture it once, store
it as a secret, and mint short-lived session tokens from it on every cloud run.

**That fails because the refresh token's advertised lifetime is a lie.** The API
returns `refreshTokenExpiresIn: 31536000` (365 days), but the token actually dies
**~1 hour** after login — its real lifetime is tied to the parent Okta SSO session,
not the advertised value (measured 2026-06-23/25). So any scheduled GitHub Actions
run fires long after the token is dead and 401s.

There is no grant-based keep-alive that beats this (tested — frequent refreshes do
not reset the SSO session's clock). The only reliable path is a human Okta login
followed by booking **within the hour** — which is exactly what `book_week.py` does,
exploiting the 7-day window to cover a whole week per login.

## Check-in ⚠️

Check-in must happen in the **9:15–9:45 AM ET** window, and also needs a live token
— so it has the **same ~1h problem** and can't be automated either. To check in,
re-auth that morning and run:

```
python book_desk.py --checkin
```

(The `checkin-desk.yml` workflow is retained but will 401 unless the secret was
re-seeded within the last hour.)

## Files

| File | Purpose |
|------|---------|
| `book_week.py` | **Main tool** — re-auth + book the whole upcoming week locally |
| `book_desk.py` | Booking/check-in primitives; mints session tokens from a refresh token |
| `refresh_token.py` | Capture a refresh token and store it as the `APPSPACE_REFRESH_TOKEN` secret (only needed for the cloud dispatch backup) |
| `.github/workflows/book-desk.yml` | Manual-dispatch backup (no schedule) |
| `.github/workflows/checkin-desk.yml` | Morning check-in (see caveat above) |

## API Endpoints Used

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v3/authorization/token` | POST | Mint session token from refresh token |
| `/api/v3/reservation/users/me/events` | GET | Check existing bookings / find today's event |
| `/api/v3/reservation/locks/resources` | POST | Lock desk |
| `/api/v3/reservation/reservations` | POST | Create reservation |
| `/api/v3/reservation/events/{id}/checkin` | POST | Check in |
