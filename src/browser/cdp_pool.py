"""Playwright CDP connection pool over AdsPower browsers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from src.adspower.client import AdsPowerClient
from src.adspower.profile_manager import AdsPowerProfileManager
from src.config.settings import AppConfig
from src.domain.models import AdsPowerProfile
from src.utils.logger import LOGGER


@dataclass
class BrowserWorker:
    """Worker encapsulating an AdsPower profile and its Playwright CDP connection."""
    profile: AdsPowerProfile
    browser: Browser
    context: BrowserContext
    page: Page
    worker_index: int


class CDPBrowserPool:
    """Manages concurrent Playwright browser sessions connected to AdsPower."""

    def __init__(
        self,
        config: AppConfig,
        client: AdsPowerClient,
        profile_manager: AdsPowerProfileManager,
    ) -> None:
        self.config = config
        self.client = client
        self.profile_manager = profile_manager
        self.playwright: Optional[Playwright] = None
        self.workers: List[BrowserWorker] = []
        self._lock = asyncio.Lock()

    async def initialize(self) -> List[BrowserWorker]:
        """Launch active profiles and establish CDP connections in parallel batches."""
        active_profiles = self.profile_manager.get_active_profiles()
        if not active_profiles:
            LOGGER.error("No active AdsPower profiles to initialize!")
            return []

        LOGGER.info(f"Starting browser pool for {len(active_profiles)} active profiles...")
        self.playwright = await async_playwright().start()

        # Launch profiles in batches of 3 to avoid overwhelming local system
        batch_size = 3
        workers: List[BrowserWorker] = []

        for i in range(0, len(active_profiles), batch_size):
            batch = active_profiles[i : i + batch_size]
            tasks = [
                self._connect_profile(profile, worker_index=i + offset + 1)
                for offset, profile in enumerate(batch)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, BrowserWorker):
                    workers.append(res)
                elif isinstance(res, Exception):
                    LOGGER.error(f"Failed to initialize worker: {res}")
            await asyncio.sleep(0.5)

        self.workers = workers
        LOGGER.info(f"CDP Browser Pool initialized with {len(self.workers)} active workers.")
        return self.workers

    async def _connect_profile(
        self,
        profile: AdsPowerProfile,
        worker_index: int,
    ) -> Optional[BrowserWorker]:
        """Start single AdsPower profile and connect via CDP."""
        try:
            ws_url = await self.client.start_browser(
                user_id=profile.user_id,
                open_tabs=1,
                headless=self.config.headless,
            )
            if not ws_url:
                raise RuntimeError(f"AdsPower failed to start profile {profile.user_id}")

            profile.ws_endpoint = ws_url
            profile.is_open = True

            assert self.playwright is not None
            browser = await self.playwright.chromium.connect_over_cdp(
                ws_url,
                slow_mo=self.config.slowmo_ms,
                timeout=self.config.nav_timeout,
            )

            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = context.pages[0] if context.pages else await context.new_page()

            page.set_default_navigation_timeout(self.config.nav_timeout)
            page.set_default_timeout(self.config.click_timeout)

            LOGGER.info(
                f"[Worker #{worker_index}] Connected profile '{profile.name}' (ID: {profile.user_id}) "
                f"via proxy {profile.proxy_key}"
            )

            return BrowserWorker(
                profile=profile,
                browser=browser,
                context=context,
                page=page,
                worker_index=worker_index,
            )
        except Exception as exc:
            LOGGER.error(f"Error connecting profile {profile.user_id}: {exc}")
            return None

    async def close_all(self) -> None:
        """Gracefully close all browser connections and stop AdsPower processes."""
        LOGGER.info("Closing all CDP Browser workers and AdsPower instances...")
        for worker in self.workers:
            try:
                await worker.browser.close()
            except Exception:
                pass
            try:
                await self.client.stop_browser(worker.profile.user_id)
            except Exception:
                pass
            worker.profile.is_open = False

        self.workers.clear()

        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass
            self.playwright = None
        LOGGER.info("All browser workers closed successfully.")
