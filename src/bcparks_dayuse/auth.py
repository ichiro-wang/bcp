import asyncio
from pathlib import Path

from playwright.async_api import Browser, Page, async_playwright

from bcparks_dayuse.config import BASE_URL


class NotLoggedInError(RuntimeError):
    def __init__(self, detail: str = "Session expired or missing") -> None:
        super().__init__(f"{detail}; run `main login`.")


async def login(state_path: Path) -> None:
    """Open a visible browser so the user can log in, then save the session."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(BASE_URL)
        print("Log in in the browser window, then press Enter here to save the session.")
        await asyncio.to_thread(input)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=state_path)
        await browser.close()
    print(f"Saved session to {state_path}")


async def ensure_logged_in(page: Page) -> None:
    """On any site page, raise NotLoggedInError unless the account menu is shown.

    The header's account button reads "Log in" when logged out and shows the
    user's initials when logged in.
    """
    await page.wait_for_load_state("networkidle")
    account = page.locator(".account-btn").first
    await account.wait_for()
    if (await account.inner_text()).strip().lower() == "log in":
        raise NotLoggedInError()


async def verify_session(browser: Browser, state_path: Path) -> None:
    """Check the saved session still works, refreshing the saved tokens."""
    if not state_path.exists():
        raise NotLoggedInError(f"No saved session at {state_path}")
    context = await browser.new_context(storage_state=state_path)
    try:
        page = await context.new_page()
        await page.goto(BASE_URL)
        await ensure_logged_in(page)
        await context.storage_state(path=state_path)
    finally:
        await context.close()
