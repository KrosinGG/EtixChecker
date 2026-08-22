import asyncio
import sys
from pathlib import Path

# Ensure UTF-8 output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.adspower.client import AdsPowerClient
from src.config.settings import CONFIG


async def check_profiles_kernels():
    client = AdsPowerClient()
    profiles = await client.get_profiles_by_group(group_name=CONFIG.adspower_group_name)
    print(f"Total profiles in group: {len(profiles)}")

    working = []
    need_download = []

    for p in profiles:
        uid = p["user_id"]
        name = p.get("name")
        res = await client._request("GET", "/api/v1/browser/start", params={"user_id": uid, "open_tabs": 1})
        if res.get("code") == 0:
            ws = res.get("data", {}).get("ws", {}).get("puppeteer")
            print(f"[OK] Profile #{name} ({uid}) started! WS={ws}")
            working.append((uid, name))
            await client.stop_browser(uid)
        else:
            msg = res.get("msg")
            print(f"[FAIL] Profile #{name} ({uid}): {msg}")
            need_download.append((uid, name, msg))

    print(f"\nSummary: {len(working)} working, {len(need_download)} failed.")


if __name__ == "__main__":
    asyncio.run(check_profiles_kernels())
