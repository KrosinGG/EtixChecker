import asyncio
import sys
from pathlib import Path

# Ensure UTF-8 output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.settings import CONFIG
from src.domain.models import CheckResult
from src.etix.checker import EtixCheckEngine


async def test_engine():
    print("🚀 Running EtixCheckEngine on real show...")
    engine = EtixCheckEngine(config=CONFIG)

    def on_done(res: CheckResult, cur: int, total: int):
        print(f"[{cur}/{total}] RESULT: status={res.status.value}, reserved={res.reserved}/{res.target}, details={res.details}")

    results = await engine.run(
        shows_csv=CONFIG.shows_csv,
        resume=False,
        on_show_done=on_done,
    )
    print("Done! Total results:", len(results))
    for r in results:
        print("Final:", r.to_report_row())


if __name__ == "__main__":
    asyncio.run(test_engine())
