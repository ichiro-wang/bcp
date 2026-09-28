# bcparks-dayuse

Watches https://reserve.bcparks.ca/dayuse/ for day-use pass availability, shows a desktop
notification when passes open up, and can book one automatically.

## Setup

```sh
uv sync
uv run playwright install chromium
```

## Scanning

```sh
uv run main scan --date 2026-10-03 --once                # single scan
uv run main scan --date 2026-10-03 --date 2026-10-04     # poll every 5 minutes
uv run main scan --date 2026-10-03 --interval 60         # poll every minute
```

Each date is reported as one of: available (with count), sold out, booking not open yet
(with the opening time — bookings open at 7:00 AM Pacific two days before), booking closed,
or no passes offered (days when passes aren't required).

When a date's booking window hasn't opened yet, the scanner wakes up right as it opens
rather than waiting for the next interval. It stops on its own if none of the dates can
become available.

A desktop notification is shown when a date becomes available (Windows toast on
Windows/WSL, `notify-send` on Linux).

Scanning uses the site's public API and needs no login. The product scanned is set in
`src/bcparks_dayuse/config.py` (currently the Rubble Creek day-use vehicle pass).

## Booking automatically

Booking needs a logged-in session. A browser window opens; log in, then press Enter in the
terminal. The session is saved to `.auth/state.json` (git-ignored).

```sh
uv run main login
```

Then add `--book` and your vehicle details. The first available date is booked and the
scanner stops:

```sh
uv run main scan --date 2026-10-03 --date 2026-10-04 --book --plate ABC123
uv run main scan --date 2026-10-03 --book --plate ABC123 --dry-run --headed   # stop before Finish
```

`--province` defaults to "British Columbia". The session is checked before scanning starts,
so an expired login is caught up front; re-run `login` if that happens.

Bookings open at 7:00 AM Pacific two days before the visit. If you start the scanner before
then, it books at the opening instead of waiting to see passes in a scan: a minute early it
refreshes the login and fills in the park and pass type, then at 7:00 (by the site's clock,
correcting for any difference in yours) it selects the date and checks out, reloading until
the book button appears. Leave it running overnight; the computer must stay awake.
