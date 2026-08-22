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


async def dump_mui():
    client = AdsPowerClient()
    profiles = await client.get_profiles_by_group(group_name=CONFIG.adspower_group_name)
    p0 = profiles[0]
    uid = p0["user_id"]
    ws = await client.start_browser(user_id=uid, open_tabs=1, headless=False)

    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(ws)
        context = browser.contexts[0]
        page = context.pages[0]

        url = "https://www.etix.com/ticket/p/35196855/nate-smith-palmer-alaska-state-fair"
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(4)

        quantities = await page.locator(".smoketest-ticket-quantity").all()
        print(f"Total .smoketest-ticket-quantity elements: {len(quantities)}")
        for idx, q in enumerate(quantities):
            h = await q.inner_html()
            print(f"--- Quantity #{idx} HTML ---")
            print(h)

        combos = await page.locator("[role='combobox'], .MuiSelect-select, select").all()
        print(f"Total dropdown/combobox elements: {len(combos)}")
        for idx, c in enumerate(combos):
            tag = await c.evaluate("el => el.tagName")
            role = await c.get_attribute("role")
            cls = await c.get_attribute("class")
            txt = await c.inner_text()
            print(f"  Combo #{idx}: tag={tag}, role={role}, text={repr(txt)}, class={cls}")

        # Let's test clicking the 2nd combobox (Lawn Area) and selecting 4
        if len(combos) >= 2:
            print("Clicking combo #1 (Lawn Area)...")
            await combos[1].click()
            await asyncio.sleep(1)
            options = await page.locator("li[role='option'], .MuiMenuItem-root").all()
            print(f"Opened menu options count: {len(options)}")
            for opt in options:
                print("  Option text:", repr(await opt.inner_text()))
            # Click option '4'
            opt_4 = page.locator("li[role='option']:has-text('4'), .MuiMenuItem-root:has-text('4')").first
            if await opt_4.is_visible():
                await opt_4.click()
                print("Successfully selected option 4!")
                await asyncio.sleep(1)
                print("Combo #1 text now:", repr(await combos[1].inner_text()))

        await browser.close()
    finally:
        await client.stop_browser(uid)
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(dump_mui())
