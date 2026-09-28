import asyncio
import enum
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from playwright.async_api import APIRequestContext, Browser, async_playwright

from bcparks_dayuse.auth import verify_session
from bcparks_dayuse.booker import book
from bcparks_dayuse.config import API_URL, Product, Settings
from bcparks_dayuse.notify import notify

log = logging.getLogger(__name__)


class Status(enum.Enum):
    AVAILABLE = "available"
    SOLD_OUT = "sold out"
    NOT_OPEN_YET = "booking not open yet"
    BOOKING_CLOSED = "booking closed"
    NO_PASSES = "no passes offered"  # e.g. weekdays when passes aren't required


# Statuses that can never change to AVAILABLE.
FINAL_STATUSES = {Status.BOOKING_CLOSED, Status.NO_PASSES}

# How long before booking opens to refresh the login and fill in the booking form.
PREPARE_AHEAD = timedelta(minutes=1)
# Warn if the local clock differs from the site's by more than this.
MAX_CLOCK_SKEW = timedelta(seconds=2)


@dataclass(frozen=True)
class ScanResult:
    date: date
    status: Status
    available: int = 0
    opens_at: datetime | None = None

    def __str__(self) -> str:
        match self.status:
            case Status.AVAILABLE:
                return f"{self.date}: {self.available} passes available"
            case Status.NOT_OPEN_YET:
                return f"{self.date}: {self.status.value} (opens {self.opens_at:%Y-%m-%d %H:%M %Z})"
            case _:
                return f"{self.date}: {self.status.value}"


async def get_response(api: APIRequestContext, path: str, params: dict[str, str] | None = None) -> dict:
    response = await api.get(API_URL + path, params=params)
    if not response.ok:
        raise RuntimeError(f"GET {response.url} returned {response.status}")
    return await response.json()


async def get_json(api: APIRequestContext, path: str, params: dict[str, str]) -> object:
    return (await get_response(api, path, params))["data"]


async def check_clock(api: APIRequestContext) -> timedelta:
    """Return how far the site's clock is ahead of the local one."""
    before = datetime.now().astimezone()
    body = await get_response(api, "waiting-room/mode2/status")
    after = datetime.now().astimezone()
    server = datetime.fromtimestamp(body["serverTime"] / 1000).astimezone()
    skew = server - (before + (after - before) / 2)
    if abs(skew) > MAX_CLOCK_SKEW:
        log.warning("Local clock is off from the site's by %.1fs; correcting for it", skew.total_seconds())
    return skew


def product_path(endpoint: str, product: Product) -> str:
    return f"{endpoint}/{product.collection_id}/dayuse/{product.activity_id}/{product.product_id}"


async def fetch_reservation_windows(
    api: APIRequestContext, product: Product, dates: tuple[date, ...]
) -> dict[date, tuple[datetime, datetime]]:
    """Map each date to the (open, close) window in which it can be booked."""
    items = await get_json(
        api,
        product_path("product-dates", product),
        {"startDate": min(dates).isoformat(), "endDate": max(dates).isoformat()},
    )
    windows = {}
    for item in items:
        window = item["reservationContext"]["temporalWindows"]["reservationWindow"]
        windows[date.fromisoformat(item["date"])] = (
            datetime.fromtimestamp(window["open"] / 1000).astimezone(),
            datetime.fromtimestamp(window["close"] / 1000).astimezone(),
        )
    return windows


async def check_date(
    api: APIRequestContext,
    product: Product,
    visit_date: date,
    window: tuple[datetime, datetime] | None,
) -> ScanResult:
    pool = await get_json(api, product_path("inventoryPools", product), {"date": visit_date.isoformat()})
    if window is None or not pool["isOpen"]:
        return ScanResult(visit_date, Status.NO_PASSES)

    opens_at, closes_at = window
    now = datetime.now().astimezone()
    if now < opens_at:
        return ScanResult(visit_date, Status.NOT_OPEN_YET, pool["available"], opens_at)
    if now >= closes_at:
        return ScanResult(visit_date, Status.BOOKING_CLOSED)
    if pool["available"] <= 0:
        return ScanResult(visit_date, Status.SOLD_OUT)
    return ScanResult(visit_date, Status.AVAILABLE, pool["available"])


async def scan_once(api: APIRequestContext, settings: Settings) -> list[ScanResult]:
    windows = await fetch_reservation_windows(api, settings.product, settings.dates)
    return list(
        await asyncio.gather(
            *(check_date(api, settings.product, d, windows.get(d)) for d in settings.dates)
        )
    )


def seconds_until_next_scan(results: list[ScanResult], interval: int) -> float:
    """The regular interval, shortened to wake right as a booking window opens."""
    now = datetime.now().astimezone()
    openings = [
        (r.opens_at - now).total_seconds() + 1
        for r in results
        if r.status is Status.NOT_OPEN_YET and r.opens_at
    ]
    return max(1.0, min([interval, *openings]))


def next_opening(results: list[ScanResult]) -> ScanResult | None:
    upcoming = [r for r in results if r.status is Status.NOT_OPEN_YET and r.opens_at]
    return min(upcoming, key=lambda r: r.opens_at, default=None)


def opens_soon(result: ScanResult, interval: int) -> bool:
    """Whether it's time to prepare for booking before the next regular scan."""
    assert result.opens_at is not None
    prepare_at = result.opens_at - PREPARE_AHEAD
    return prepare_at - datetime.now().astimezone() <= timedelta(seconds=interval)


async def sleep_until(when: datetime) -> None:
    await asyncio.sleep(max(0.0, (when - datetime.now().astimezone()).total_seconds()))


async def try_booking(
    browser: Browser, settings: Settings, visit_date: date, opens_at: datetime | None = None
) -> bool:
    log.info("Booking %s...", visit_date)
    try:
        confirmation = await book(browser, settings, visit_date, opens_at)
    except Exception as e:
        log.exception("Booking %s failed", visit_date)
        await notify("BC Parks booking failed", f"{visit_date}: {e}")
        return False
    if settings.dry_run:
        await notify("BC Parks dry run finished", f"{visit_date}: stopped before Finish")
    else:
        log.info("Booked %s: %s", visit_date, confirmation)
        await notify("BC Parks pass booked!", f"{settings.product.park_name} on {visit_date}")
    return True


async def book_at_opening(
    browser: Browser, api: APIRequestContext, settings: Settings, result: ScanResult
) -> bool:
    """Get ready shortly before result's booking window opens, then book right as it does."""
    assert result.opens_at is not None
    log.info("Will book %s when booking opens at %s", result.date, f"{result.opens_at:%Y-%m-%d %H:%M %Z}")
    await sleep_until(result.opens_at - PREPARE_AHEAD)
    # Opening is by the site's clock; convert to the local clock.
    opens_at_local = result.opens_at - await check_clock(api)
    return await try_booking(browser, settings, result.date, opens_at_local)


async def run(settings: Settings) -> None:
    async with async_playwright() as pw:
        api = await pw.request.new_context(timeout=settings.timeout_ms)
        browser = None
        if settings.vehicle:
            browser = await pw.chromium.launch(headless=settings.headless)
            await verify_session(browser, settings.state_path)
            await check_clock(api)
            log.info("Logged in; will book the first available date")

        notified: set[date] = set()
        try:
            while True:
                try:
                    results = await scan_once(api, settings)
                except Exception:
                    log.exception("Scan failed")
                    results = []

                for result in results:
                    log.info("%s", result)
                available = [r for r in results if r.status is Status.AVAILABLE]

                for result in available:
                    if result.date not in notified:
                        notified.add(result.date)
                        await notify("BC Parks pass available", str(result))
                notified &= {r.date for r in available}

                if browser and available and await try_booking(browser, settings, available[0].date):
                    break
                if settings.once:
                    break
                if results and all(r.status in FINAL_STATUSES for r in results):
                    log.info("None of the dates can become available; stopping.")
                    break

                # Rather than waiting to see passes in a scan, book the moment they open.
                upcoming = next_opening(results)
                if browser and upcoming and opens_soon(upcoming, settings.interval_seconds):
                    if await book_at_opening(browser, api, settings, upcoming):
                        break
                    continue  # rescan straight away

                await asyncio.sleep(seconds_until_next_scan(results, settings.interval_seconds))
        finally:
            await api.dispose()
            if browser:
                await browser.close()
