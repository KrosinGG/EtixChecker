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

    async def initialize(self, count_needed: Optional[int] = None) -> List[BrowserWorker]:
        """
        Launch active profiles and establish CDP connections in parallel batches.
        If count_needed is specified, randomly samples that number of profiles from active list.
        """
        import random

        active_profiles = self.profile_manager.get_active_profiles()
        if not active_profiles:
            LOGGER.error("No active AdsPower profiles to initialize!")
            return []

        # If only a subset is needed, pick a randomized sample
        if count_needed is not None and 0 < count_needed < len(active_profiles):
            selected_profiles = random.sample(active_profiles, count_needed)
            LOGGER.info(
                f"Selected {len(selected_profiles)} randomized profiles for this run "
                f"(needed: {count_needed}, total available: {len(active_profiles)})"
            )
        else:
            selected_profiles = list(active_profiles)
            LOGGER.info(f"Starting browser pool for all {len(selected_profiles)} active profiles...")

        self.playwright = await async_playwright().start()

        # Launch profiles in batches of 3 to avoid overwhelming local system
        batch_size = 3
        workers: List[BrowserWorker] = []

        for i in range(0, len(selected_profiles), batch_size):
            batch = selected_profiles[i : i + batch_size]
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
            
            # Tab sanitization: ensure single active clean page
            pages = context.pages
            if not pages:
                page = await context.new_page()
            else:
                page = pages[0]
                # If AdsPower opened extra blank or start tabs, close them gracefully
                if len(pages) > 1:
                    for extra_page in pages[1:]:
                        try:
                            await extra_page.close()
                        except Exception:
                            pass

            try:
                await page.bring_to_front()
            except Exception:
                pass

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

    async def replace_worker_with_reserve(
        self,
        failing_worker: BrowserWorker,
        reason: str = "DataDome block",
    ) -> Optional[BrowserWorker]:
        """
        Close a failing worker, mark its proxy bad for this session,
        allocate a reserve profile with a clean good proxy, and start a new worker.
        """
        async with self._lock:
            LOGGER.warning(
                f"Initiating Hot-Swap for Worker #{failing_worker.worker_index} ({failing_worker.profile.user_id}). Reason: {reason}"
            )
            # 1. Record failing proxy as session bad
            if failing_worker.profile.proxy_key:
                self.profile_manager.record_bad_proxy(failing_worker.profile.proxy_key, reason)

            # 2. Close failing browser
            try:
                await failing_worker.browser.close()
                await self.client.stop_browser(failing_worker.profile.user_id)
            except Exception:
                pass

            # 3. Find next reserve profile
            reserve_prof = self.profile_manager.get_next_available_reserve()
            if not reserve_prof:
                LOGGER.error("No reserve profiles available for hot-swap!")
                return None

            # 4. Assign good proxy to reserve profile via AdsPower API
            await self.profile_manager.setup_reserve_profile_with_good_proxy(reserve_prof)

            # 5. Connect new reserve profile
            new_worker = await self._connect_profile(reserve_prof, worker_index=failing_worker.worker_index)
            if not new_worker:
                LOGGER.error(f"Failed to connect reserve profile {reserve_prof.user_id}")
                return None

            # 6. Update workers list
            if failing_worker in self.workers:
                idx = self.workers.index(failing_worker)
                self.workers[idx] = new_worker

            LOGGER.info(
                f"Hot-swap complete! Worker #{failing_worker.worker_index} is now profile '{reserve_prof.name}' ({reserve_prof.user_id})"
            )
            return new_worker

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

        # Extra safety check: ensure all active browsers in AdsPower are shut down
        try:
            await self.client.stop_all_active_browsers()
        except Exception:
            pass

        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass
            self.playwright = None
        LOGGER.info("All browser workers closed successfully.")
