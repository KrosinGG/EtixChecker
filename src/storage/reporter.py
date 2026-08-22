"""Reporter for saving check results to report.csv and evidence screenshots."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional
import pandas as pd
from playwright.async_api import Page

from src.domain.models import CheckResult
from src.utils.logger import LOGGER

REPORT_CSV = Path("report.csv")
SCREENS_DIR = Path("screens")
RERUN_SHOWS_CSV = Path("data/shows_rerun.csv")


class Reporter:
    """Handles report generation, rerun exports, and screenshot capture."""

    def __init__(
        self,
        report_path: Path = REPORT_CSV,
        screens_dir: Path = SCREENS_DIR,
    ) -> None:
        self.report_path = report_path
        self.screens_dir = screens_dir
        self.screens_dir.mkdir(parents=True, exist_ok=True)

    async def save_screenshot(self, page: Page, show_name: str, prefix: str = "state") -> Optional[str]:
        """Capture and save full-page screenshot as evidence."""
        try:
            safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in show_name)[:30]
            filename = f"{prefix}_{safe_name}.png"
            target_path = self.screens_dir / filename
            await page.screenshot(path=str(target_path), full_page=False)
            LOGGER.debug(f"Saved screenshot to {target_path}")
            return str(target_path)
        except Exception as exc:
            LOGGER.warning(f"Could not take screenshot for {show_name}: {exc}")
            return None

    def save_report(self, results: List[CheckResult]) -> Path:
        """Write summary report.csv (excluding internal url column)."""
        rows = [r.to_report_row() for r in results]
        if not rows:
            df = pd.DataFrame(
                columns=["name", "status", "target", "reserved", "available_approx", "details", "notes"]
            )
        else:
            df = pd.DataFrame(rows)

        # Ensure 'url' column is strictly excluded from user-facing report
        if "url" in df.columns:
            df = df.drop(columns=["url"])

        df.to_csv(self.report_path, index=False, encoding="utf-8-sig")
        LOGGER.info(f"Generated final report: {self.report_path} ({len(rows)} entries)")
        return self.report_path

    def save_rerun_file(self, failed_shows_data: List[Dict[str, Any]]) -> Optional[Path]:
        """Save shows that were insufficient or failed for quick rerun."""
        if not failed_shows_data:
            return None
        RERUN_SHOWS_CSV.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(failed_shows_data)
        df.to_csv(RERUN_SHOWS_CSV, index=False, encoding="utf-8-sig")
        LOGGER.info(f"Saved rerun file with {len(failed_shows_data)} shows to {RERUN_SHOWS_CSV}")
        return RERUN_SHOWS_CSV
