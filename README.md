# Appspace Desk Auto-Booker 🏢

Automatically books desk **08W-125-G** at 7 Hudson, 7 days in advance using GitHub Actions.

## Configuration

| Setting | Value |
|---------|-------|
| Desk | 08W-125-G (8th floor, 7 Hudson) |
| Time | 9:30 AM - 5:30 PM Eastern |
| Booking Window | 7 days in advance |

## Automated Schedules

| Workflow | Schedule | Purpose |
|----------|----------|---------|
| **Book Desk Daily** | 11:00 PM ET (Mon-Thu) | Books desk 7 days in advance (runs night before to beat other bookers) |
| **Check In to Desk** | 8:00 AM ET (Mon-Thu) | Waits for the 9:15-9:45 AM check-in window, then checks in |

There is **no keep-alive workflow**. The Appspace session token has a short
(~90-min) TTL and pinging it does not extend it, so instead of trying to keep a
token warm 24/7, each workflow **mints a fresh token on-demand** (headless Okta
re-auth via Playwright) immediately before it books or checks in. The token is
only ever seconds old when used.

## Setup

### 1. Required Secrets

Go to **Settings → Secrets and variables → Actions** and add:

| Secret Name | Description | How to Get |
|-------------|-------------|------------|
| `APPSPACE_SESSION_TOKEN` | Latest session token (fallback / inspection) | Auto-managed by `refresh_token.py` |
| `PLAYWRIGHT_AUTH_STATE` | Stored Okta browser session for headless mints | Auto-seeded by `refresh_token.py` |
| `GH_PAT` | PAT with `secrets` write, so CI can update the secrets above | Create a fine-grained PAT |

### 2. Getting Your Tokens

Run the refresher once locally — it captures the token **and** seeds
`PLAYWRIGHT_AUTH_STATE` so CI can mint headlessly afterward:

```
python refresh_token.py
```

Complete the Disney SSO / Okta push in the browser window that opens. The script
validates the token, then writes both `APPSPACE_SESSION_TOKEN` and
`PLAYWRIGHT_AUTH_STATE` to GitHub Secrets.

### 3. Manual Run

You can trigger the workflow manually:
1. Go to **Actions** tab
2. Select "Book Desk Daily"
3. Click "Run workflow"

## How It Works

**Booking (11:00 PM ET, Mon-Thu)**
```
┌─────────────────────────────────────────────────────────────┐
│  1. Mint a fresh token (headless Okta re-auth)              │
│  2. Check if YOU already have desk booked for target date   │
│  3. Lock the desk resource                                  │
│  4. Create reservation 7 days out                           │
│  5. On 409 conflict, VERIFY you have the desk (fail if not) │
└─────────────────────────────────────────────────────────────┘
```

**Check-in (8:00 AM ET, Mon-Thu)**
```
┌─────────────────────────────────────────────────────────────┐
│  1. Sleep until the 9:15 AM window opens (no token needed)  │
│     — absorbs GitHub's cron delay (often 30-60+ min late)   │
│  2. Mint a fresh token (so it's seconds old in the window)  │
│  3. Find today's reservation and check in                  │
└─────────────────────────────────────────────────────────────┘
```

### Token lifecycle

The only thing that needs occasional manual attention is the **Okta session**
stored in `PLAYWRIGHT_AUTH_STATE`. Each successful headless mint re-seeds it, so
it stays warm — but SSO has a hard max age. When it finally expires, the next
booking/check-in opens a GitHub Issue labeled `token-expired`, and the local
monitor prompts you to run `python refresh_token.py` once and approve the Okta
push. That issue auto-closes on the next successful mint.

## Troubleshooting

### Token Expired (401 Error)
1. Log into Appspace in your browser
2. Get a fresh token from DevTools
3. Update `APPSPACE_SESSION_TOKEN` in GitHub Secrets

### Desk Already Booked (409 Conflict)
The script will verify if YOU have the reservation:
- If you have it: Success ✅
- If someone else has it: Failure ❌ (consider running earlier)

### Check-In Failed
- The check-in job is scheduled at 8:00 AM ET and sleeps until the 9:15-9:45 AM
  ET window before checking in, so it tolerates GitHub cron delays up to ~75 min.
- If the job logs `TOKEN EXPIRED`, the on-demand mint failed — see the
  `token-expired` issue and run `python refresh_token.py` locally.
- If GitHub delays the run past ~9:45 AM ET, the window is already closed and
  Appspace will reject the check-in; re-run the workflow manually if needed.

### Workflow Not Running
- Check that Actions are enabled for the repository
- Verify the cron schedule is correct
- Check the Actions tab for any errors

## Files

| File | Purpose |
|------|---------|
| `book_desk.py` | Main booking script |
| `.github/workflows/book-desk.yml` | GitHub Actions workflow |
| `README.md` | This file |

## API Endpoints Used

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v3/authorization/token` | POST | Refresh tokens |
| `/api/v3/reservation/users/me/events` | GET | Check existing bookings |
| `/api/v3/reservation/locks/resources` | POST | Lock desk |
| `/api/v3/reservation/reservations` | POST | Create reservation |

