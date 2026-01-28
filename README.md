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
| **Book Desk Daily** | 11:00 PM ET (Sun-Thu) | Books desk 7 days in advance (runs night before to beat other bookers) |
| **Check In to Desk** | 9:20 AM ET (Mon-Fri) | Auto check-in (within 9:00-10:00 AM window) |

## Setup

### 1. Required Secrets

Go to **Settings → Secrets and variables → Actions** and add:

| Secret Name | Description | How to Get |
|-------------|-------------|------------|
| `APPSPACE_SESSION_TOKEN` | Session token | See below |
| `APPSPACE_REFRESH_TOKEN` | Refresh token (optional) | See below |

### 2. Getting Your Tokens

1. Open https://disney.cloud.appspace.com in Chrome
2. Open DevTools (F12) → Network tab
3. Click on any page/make a reservation
4. Find a request to `/api/v3/*`
5. Copy the `token` header value → This is your `APPSPACE_SESSION_TOKEN`

For the refresh token:
1. Find a request to `/api/v3/authorization/token`
2. Look at the response body for `refreshToken`

### 3. Manual Run

You can trigger the workflow manually:
1. Go to **Actions** tab
2. Select "Book Desk Daily"
3. Click "Run workflow"

## How It Works

```
┌─────────────────────────────────────────────────────────────┐
│  GitHub Actions (runs at 11:00 PM ET, Sun-Thu)              │
├─────────────────────────────────────────────────────────────┤
│  1. Check if YOU already have desk booked for target date   │
│  2. Lock the desk resource                                  │
│  3. Create reservation 7 days out                           │
│  4. On 409 conflict, VERIFY you have the desk (fail if not) │
│  5. Log results                                             │
└─────────────────────────────────────────────────────────────┘
```

## Troubleshooting

### Token Expired (401 Error)
1. Log into Appspace in your browser
2. Get a fresh token from DevTools
3. Update `APPSPACE_SESSION_TOKEN` in GitHub Secrets

### Desk Already Booked (409 Conflict)
The script will verify if YOU have the reservation:
- If you have it: Success ✅
- If someone else has it: Failure ❌ (consider running earlier)

### Check-In Failed / Too Early
- Workflow now runs at 9:20 AM ET directly (no longer relies on queue delays)
- Check-in window is 9:00 AM - 10:00 AM ET (30 min buffer on each side)
- If check-in fails with "too early", the cron timing may have drifted

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

