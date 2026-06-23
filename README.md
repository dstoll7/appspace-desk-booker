# Appspace Desk Auto-Booker 🏢

Automatically books desk **08W-125-G** at 7 Hudson, 7 days in advance, and checks
in each morning — **fully unattended** via GitHub Actions. No browser, no daily
logins, no laptop required.

## Configuration

| Setting | Value |
|---------|-------|
| Desk | 08W-125-G (8th floor, 7 Hudson) |
| Time | 9:30 AM - 5:30 PM Eastern |
| Booking Window | 7 days in advance |

## Automated Schedules

| Workflow | Schedule | Purpose |
|----------|----------|---------|
| **Book Desk Daily** | 11:00 PM ET (Mon-Thu) | Books the desk 7 days out |
| **Check In to Desk** | 8:00 AM ET (Mon-Thu) | Waits for the 9:15-9:45 AM window, then checks in |

## How authentication works (the important part)

Appspace session tokens are short-lived and can **only** be minted interactively
through Disney's Okta SSO — there is no headless/automated browser path (Okta
blocks it). So keeping a session token alive in the cloud is impossible.

Instead, this project uses Appspace's **refresh token**:

- A one-time local login (`refresh_token.py`) captures a **refresh token** that
  is valid for **~365 days** and is **non-rotating**.
- It's stored as the `APPSPACE_REFRESH_TOKEN` GitHub Secret.
- Every workflow run mints a fresh short-lived session token from it with a
  single plain-HTTP call (`POST /api/v3/authorization/token`,
  `grantType: refreshToken`) — **no browser, no Okta, no interaction.**

Result: the system runs hands-off for ~a year. You only re-authenticate when the
refresh token finally expires or is revoked.

## Setup

### 1. Seed the refresh token (once, locally)

```
python refresh_token.py
```

Complete the Disney/Okta login in the window that opens. The script captures the
refresh token, validates it, and stores it as the `APPSPACE_REFRESH_TOKEN`
secret. That's it — booking and check-in now run automatically.

### 2. Secret

| Secret | Description |
|--------|-------------|
| `APPSPACE_REFRESH_TOKEN` | Long-lived (~365-day) refresh token. Set by `refresh_token.py`. |

(`GITHUB_TOKEN` is provided automatically and is used only to open a reminder
issue if the refresh token ever expires.)

### 3. Manual run

Actions tab → select a workflow → **Run workflow**.

## How It Works

**Booking (11:00 PM ET, Mon-Thu)**
```
1. Mint a fresh session token from the refresh token (plain HTTP)
2. Skip if you already have the desk for the target date
3. Lock the resource and create the reservation 7 days out
4. On 409, verify YOU hold the desk (fail if someone else does)
```

**Check-in (8:00 AM ET, Mon-Thu)**
```
1. Sleep until the 9:15 AM window opens (absorbs GitHub's cron delay; no token needed)
2. Mint a fresh session token from the refresh token
3. Find today's reservation and check in
```

## Troubleshooting

### `token-expired` issue appears / "AUTH FAILURE" in logs
The refresh token expired (~yearly) or was revoked. Run `python refresh_token.py`
locally once and approve the Okta push. The issue resolves on the next run.

### Check-in failed
- The job waits for the 9:15-9:45 AM window, tolerating cron delays up to ~75 min.
- If GitHub delays the run past ~9:45 AM ET, the window is closed and Appspace
  rejects the check-in — re-run the workflow manually that day.

### Desk already booked (409)
The script verifies whether *you* hold the reservation — success if yes, failure
(someone else grabbed it) if no.

## Files

| File | Purpose |
|------|---------|
| `book_desk.py` | Booking + check-in; mints session tokens from the refresh token |
| `refresh_token.py` | One-time/yearly local tool to capture & store the refresh token |
| `.github/workflows/book-desk.yml` | Nightly booking workflow |
| `.github/workflows/checkin-desk.yml` | Morning check-in workflow |

## API Endpoints Used

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v3/authorization/token` | POST | Mint session token from refresh token |
| `/api/v3/reservation/users/me/events` | GET | Check existing bookings / find today's event |
| `/api/v3/reservation/locks/resources` | POST | Lock desk |
| `/api/v3/reservation/reservations` | POST | Create reservation |
| `/api/v3/reservation/events/{id}/checkin` | POST | Check in |
