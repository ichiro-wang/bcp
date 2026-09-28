import argparse
import asyncio
import logging
import sys
from datetime import date
from pathlib import Path

from bcparks_dayuse.auth import NotLoggedInError, login
from bcparks_dayuse.config import DEFAULT_STATE_PATH, Settings, Vehicle
from bcparks_dayuse.scanner import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan BC Parks for day-use pass availability.")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH, help="Saved login session file")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="Log in interactively and save the session")

    scan = sub.add_parser("scan", help="Check availability, optionally on a loop")
    scan.add_argument(
        "--date", dest="dates", type=date.fromisoformat, action="append", required=True,
        help="Visit date, YYYY-MM-DD (repeat for several dates)",
    )
    scan.add_argument("--interval", type=int, default=300, help="Seconds between scans")
    scan.add_argument("--once", action="store_true", help="Scan once and exit")

    booking = scan.add_argument_group("booking")
    booking.add_argument("--book", action="store_true", help="Book the first available date, then stop")
    booking.add_argument("--plate", help="Vehicle plate number (required with --book)")
    booking.add_argument("--province", default="British Columbia", help="Vehicle province, state or territory")
    booking.add_argument("--dry-run", action="store_true", help="Go through booking but stop before Finish")
    booking.add_argument("--headed", action="store_true", help="Show the browser while booking")
    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "login":
            asyncio.run(login(args.state))
            return

        if args.book and not args.plate:
            parser.error("--book requires --plate")
        settings = Settings(
            dates=tuple(sorted(set(args.dates))),
            vehicle=Vehicle(args.plate, args.province) if args.book else None,
            dry_run=args.dry_run,
            state_path=args.state,
            headless=not args.headed,
            interval_seconds=args.interval,
            once=args.once,
        )
        asyncio.run(run(settings))
    except NotLoggedInError as e:
        sys.exit(str(e))
    except KeyboardInterrupt:
        pass
