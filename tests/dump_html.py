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


async def dump_html():
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

        # Find form or ticket area HTML
        form = await page.locator("form").all()
        print(f"Total forms: {len(form)}")
        for idx, f in enumerate(form):
            h = await f.inner_html()
            print(f"--- Form #{idx} HTML (first 1000 chars) ---")
            print(h[:1000])

        # Look for elements containing 'Lawn Area' or 'Number of Tickets'
        el = page.locator(":has-text('Number of Tickets')").last
        if await el.count() > 0:
            print("--- Container containing 'Number of Tickets' ---")
            parent = el.locator("xpath=..")
            print((await parent.inner_html())[:2000])

        await browser.close()
    finally:
        await client.stop_browser(uid)
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(dump_html())
