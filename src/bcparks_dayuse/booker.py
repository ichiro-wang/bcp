import asyncio
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from playwright.async_api import Browser, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from bcparks_dayuse.auth import ensure_logged_in
from bcparks_dayuse.config import BASE_URL, Product, Settings

log = logging.getLogger(__name__)

MONTH_BUTTON = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|October|November|December)$"
)

# The site decides a date is open using its own clock, so there's no point asking
# before opening; go just after instead.
OPENING_DELAY = timedelta(seconds=0.3)
# Give up waiting for bookings to open this long after opening time.
OPENING_GRACE = timedelta(minutes=2)


async def open_booking_form(page: Page, product: Product) -> None:
    """Load the booking page and select the park and pass type."""
    await page.goto(BASE_URL)
    await ensure_logged_in(page)
    await page.get_by_role("button", name="Select a park").click()
    await page.get_by_role("menuitem", name=product.park_name).click()
    await page.get_by_role("button", name="Pass type").click()
    await page.get_by_role("menuitem", name=product.pass_name).click()


async def select_date(page: Page, visit_date: date, timeout_ms: float | None = None) -> None:
    await page.get_by_role("button", name="Date of visit").click()
    target_month = visit_date.strftime("%B")
    for _ in range(12):
        shown = (await page.get_by_role("button", name=MONTH_BUTTON).inner_text()).strip()
        if shown == target_month:
            break
        await page.get_by_role("button", name="next calendar").click()
    else:
        raise RuntimeError(f"Could not find {target_month} in the date picker")
    day = page.locator(f'button.calendar-date.valid-date[value="{visit_date.day}"]')
    await day.click(timeout=timeout_ms)


async def select_date_at_opening(page: Page, product: Product, visit_date: date, opens_at: datetime) -> None:
    """On a prepared booking form, wait for opens_at, then select visit_date and
    reload until it can be booked.

    opens_at is in local-clock time. A date can be picked before its bookings open;
    the panel then says "not open yet" instead of offering the book button.
    """
    wait = (opens_at + OPENING_DELAY - datetime.now().astimezone()).total_seconds()
    log.info("Booking form ready; waiting %.0fs until opening", max(wait, 0))
    await asyncio.sleep(max(wait, 0))

    # Before the date is picked, a disabled "No passes available" placeholder shows,
    # so wait specifically for an enabled book button.
    book_button = page.get_by_role("button", name="Book day-use pass", disabled=False)
    not_open = page.get_by_text("not open yet")
    while True:
        try:
            await select_date(page, visit_date, timeout_ms=5_000)
            await book_button.or_(not_open).first.wait_for(timeout=10_000)
            if await book_button.is_visible():
                return
        except PlaywrightTimeoutError:
            pass
        if datetime.now().astimezone() > opens_at + OPENING_GRACE:
            raise RuntimeError(f"Bookings for {visit_date} didn't open by {opens_at + OPENING_GRACE:%H:%M:%S}")
        log.info("%s not open yet; reloading", visit_date)
        await open_booking_form(page, product)


async def go_to_cart(page: Page) -> None:
    """After clicking "Book day-use pass", get to the cart page.

    The cart holds one pending booking; if it already has one (e.g. from an earlier
    attempt that didn't finish), the site asks whether to replace it.
    """
    replace = page.get_by_role("button", name="Replace")
    checkout = page.get_by_role("button", name="Checkout")
    try:
        await replace.or_(checkout).first.wait_for(timeout=10_000)
        if await replace.is_visible():
            log.info("Replacing the pending booking already in the cart")
            await replace.click()
            await checkout.wait_for(timeout=10_000)
    except PlaywrightTimeoutError:
        log.info("Not taken to the cart; opening it directly")
        await page.goto(BASE_URL + "cart")


async def book(
    browser: Browser, settings: Settings, visit_date: date, opens_at: datetime | None = None
) -> str:
    """Book a pass for visit_date and return the confirmation page URL.

    With opens_at, the form is filled in ahead of time and the date is picked the
    moment booking opens. With settings.dry_run, stops before the final "Finish"
    click and returns the path of a screenshot instead.
    """
    assert settings.vehicle is not None
    product, vehicle = settings.product, settings.vehicle

    context = await browser.new_context(storage_state=settings.state_path)
    context.set_default_timeout(settings.timeout_ms)
    try:
        page = await context.new_page()
        await open_booking_form(page, product)
        if opens_at:
            await select_date_at_opening(page, product, visit_date, opens_at)
        else:
            await select_date(page, visit_date)

        await page.get_by_role("button", name="Book day-use pass").click()
        await go_to_cart(page)
        await page.get_by_role("button", name="Checkout").click()
        await page.locator("#acknowledgeDetails").check()

        # Continue through the intermediate steps until the vehicle form appears.
        plate = page.get_by_role("textbox", name="Plate number")
        continue_button = page.get_by_role("button", name="Continue")
        for _ in range(5):
            await continue_button.or_(plate).first.wait_for()
            if await plate.is_visible():
                break
            await continue_button.click()
            await page.wait_for_load_state()

        await plate.fill(vehicle.plate)
        province = page.get_by_role("combobox", name="Province, state or territory")
        await province.fill(vehicle.province[:3])
        await page.get_by_role("option", name=vehicle.province).click()

        if settings.dry_run:
            screenshot = Path("dry-run.png")
            await page.screenshot(path=screenshot, full_page=True)
            log.info("Dry run: stopped before Finish; screenshot saved to %s", screenshot)
            return str(screenshot)

        await page.get_by_role("button", name="Finish").click()
        await page.wait_for_url("**/booking-confirmation/**")
        return page.url
    finally:
        # Keep any refreshed login tokens for next time.
        await context.storage_state(path=settings.state_path)
        await context.close()
