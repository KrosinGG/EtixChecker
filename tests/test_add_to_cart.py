import asyncio
import sys
from pathlib import Path

# Ensure UTF-8 output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.adspower.client import AdsPowerClient
from src.config.settings import CONFIG
from playwright.async_api import async_playwright


async def test_full_add():
    client = AdsPowerClient()
    profiles = await client.get_profiles_by_group(group_name=CONFIG.adspower_group_name)
    p0 = profiles[0]
    uid = p0["user_id"]
    print(f"Starting browser profile {uid}...")
    ws = await client.start_browser(user_id=uid, open_tabs=1, headless=False)

    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(ws)
        context = browser.contexts[0]
        page = context.pages[0]

        url = "https://www.etix.com/ticket/p/35196855/nate-smith-palmer-alaska-state-fair"
        print(f"Opening {url}...")
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(3)

        # 1. Find comboboxes or selects
        combos = await page.locator("[role='combobox'], .MuiSelect-select, select").all()
        print(f"Found {len(combos)} quantity dropdowns.")
        assert len(combos) >= 2, "Expected at least 2 dropdowns"

        # 2. Select 2nd ticket type (Lawn Area -> index 1)
        target_combo = combos[1]
        await target_combo.scroll_into_view_if_needed()
        await target_combo.click()
        await asyncio.sleep(0.5)

        # 3. Pick option '4'
        opt = page.locator("li[role='option']:has-text('4'), .MuiMenuItem-root:has-text('4')").first
        await opt.click()
        print("Selected 4 tickets for Lawn Area.")
        await asyncio.sleep(1)

        # 4. Find and click 'Add Tickets'
        add_btn = page.locator("button:has-text('Add Tickets'), input[value*='Add Tickets']").first
        await add_btn.scroll_into_view_if_needed()
        print("Clicking Add Tickets...")
        await add_btn.click()

        # 5. Wait for cart
        await asyncio.sleep(4)
        print("After click URL:", page.url)
        print("After click Title:", await page.title())

        # Check cart indicators
        in_cart = "/cart" in page.url.lower() or "/checkout" in page.url.lower() or await page.locator(".cart-item, #cart-container, table.cart").first.is_visible()
        print("Successfully in Cart?:", in_cart)

        await asyncio.sleep(3)
        await browser.close()
    finally:
        await client.stop_browser(uid)
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(test_full_add())
