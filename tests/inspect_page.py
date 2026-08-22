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


async def inspect():
    client = AdsPowerClient()
    profiles = await client.get_profiles_by_group(group_name=CONFIG.adspower_group_name)
    if not profiles:
        print("No profiles found!")
        return
    p0 = profiles[0]
    uid = p0["user_id"]
    p_name = p0.get("name", "")
    print(f"Starting profile {uid} ({p_name})...")
    ws = await client.start_browser(user_id=uid, open_tabs=1, headless=False)
    print("WS endpoint:", ws)
    if not ws:
        print("Could not get WS endpoint!")
        return

    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(ws)
        print("Browser contexts count:", len(browser.contexts))
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        print("Context pages count:", len(context.pages))
        page = context.pages[0] if context.pages else await context.new_page()

        url = "https://www.etix.com/ticket/p/35196855/nate-smith-palmer-alaska-state-fair"
        print(f"Navigating to {url}...")
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(4)

        print("Current URL:", page.url)
        print("Title:", await page.title())

        # Check iframes
        frames = page.frames
        print("Total frames:", len(frames))
        for idx, f in enumerate(frames):
            print(f"  Frame #{idx}: url={f.url}")

        # Check selects in page
        selects = await page.locator("select").all()
        print("Total selects in main page:", len(selects))
        for idx, s in enumerate(selects):
            vis = await s.is_visible()
            box = await s.bounding_box()
            opts = await s.locator("option").all_inner_texts()
            print(f"  Select #{idx}: visible={vis}, box={box}, options={opts[:6]}")

        # Check all visible text around selects
        rows = await page.locator(".ticket-row, tr, div.row, li").all()
        print(f"Total potential ticket rows: {len(rows)}")

        # Check Add buttons
        btns = await page.locator("button, input[type='submit'], a.btn").all()
        print("Total buttons/links:", len(btns))
        for b in btns:
            try:
                txt = (await b.inner_text()).strip()
                vis = await b.is_visible()
                if txt:
                    print(f"  Button: '{txt}', visible={vis}")
            except Exception:
                pass

        await asyncio.sleep(2)
        await browser.close()
    finally:
        await client.stop_browser(uid)
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(inspect())
