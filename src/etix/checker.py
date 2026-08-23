"""Main check engine coordinating AdsPower workers, Etix page checks, and cart allocations."""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import random
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import pandas as pd

from src.adspower.client import AdsPowerClient
from src.adspower.profile_manager import AdsPowerProfileManager
from src.browser.cdp_pool import BrowserWorker, CDPBrowserPool
from src.browser.human_actions import (
    accept_cookies_if_present,
    close_blocking_popups,
    human_sleep,
    solve_datadome_slider,
)
from src.config.settings import AppConfig, CONFIG
from src.domain.enums import ProfileRole, ShowStatus
from src.domain.models import CheckResult, Show
from src.etix.cart_handler import EtixCartHandler
from src.etix.detector import EtixDetector
from src.storage.checkpoint import RunContext
from src.storage.proxy_sync import ProxySyncService
from src.storage.reporter import Reporter
from src.utils.logger import LOGGER

CallbackType = Optional[Callable[[CheckResult, int, int], Any]]


def make_show_id(name: str, url: str) -> str:
    """Generate deterministic show ID."""
    raw = f"{name.strip().lower()}|{url.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


class EtixCheckEngine:
    """Orchestrates multi-worker ticket availability checks via AdsPower CDP sessions."""

    def __init__(
        self,
        config: AppConfig = CONFIG,
        client: Optional[AdsPowerClient] = None,
        profile_manager: Optional[AdsPowerProfileManager] = None,
        reporter: Optional[Reporter] = None,
    ) -> None:
        self.config = config
        self.client = client or AdsPowerClient(base_url=config.adspower_api_url)
        self.profile_manager = profile_manager or AdsPowerProfileManager(
            client=self.client,
            good_proxies_file=config.good_proxies_file,
            bad_proxies_file=config.bad_proxies_file,
        )
        self.detector = EtixDetector(config)
        self.cart_handler = EtixCartHandler(config, self.detector)
        self.reporter = reporter or Reporter()
        self.cdp_pool: Optional[CDPBrowserPool] = None

    def load_shows(self, csv_path: Path) -> List[Show]:
        """Load and validate shows from CSV file."""
        if not csv_path.exists():
            LOGGER.error(f"Shows file not found: {csv_path}")
            return []

        try:
            df = pd.read_csv(csv_path).fillna("")
            df = df.rename(columns={c: c.strip().lower() for c in df.columns})
        except Exception as exc:
            LOGGER.error(f"Failed to parse shows CSV {csv_path}: {exc}")
            return []

        if "url" not in df.columns:
            LOGGER.error("Shows CSV must contain a 'url' column!")
            return []

        shows: List[Show] = []
        for _, row in df.iterrows():
            url = str(row.get("url", "")).strip()
            if not url or not url.startswith("http"):
                continue

            name = str(row.get("name", "")).strip() or url
            target_total = 1
            if "target_total" in row and str(row["target_total"]).strip().isdigit():
                target_total = max(1, int(str(row["target_total"]).strip()))

            max_per_order = 1
            if "max_per_order" in row and str(row["max_per_order"]).strip().isdigit():
                max_per_order = max(1, int(str(row["max_per_order"]).strip()))

            ticket_index = None
            if "ticket_index" in row and str(row["ticket_index"]).strip().isdigit():
                ticket_index = int(str(row["ticket_index"]).strip())

            show_id = make_show_id(name, url)
            shows.append(
                Show(
                    show_id=show_id,
                    name=name,
                    url=url,
                    target_total=target_total,
                    max_per_order=max_per_order,
                    ticket_index=ticket_index,
                    raw_row=dict(row),
                )
            )

        LOGGER.info(f"Loaded {len(shows)} valid shows from {csv_path}")
        return shows

    async def run(
        self,
        shows_csv: Path = Path("data/shows.csv"),
        resume: bool = True,
        on_show_done: CallbackType = None,
    ) -> List[CheckResult]:
        """Execute full checking pipeline across all loaded shows."""
        shows = self.load_shows(shows_csv)
        if not shows:
            return []

        # Sync good proxies from remote source (e.g. GitHub Raw / Gist) if configured
        sync_url = os.getenv("GOOD_PROXIES_SYNC_URL", None)
        proxy_sync = ProxySyncService(local_file=self.config.good_proxies_file, sync_url=sync_url)
        await proxy_sync.sync()

        # Check AdsPower connection
        if not await self.client.check_status():
            raise RuntimeError(
                f"AdsPower Local API is not reachable at {self.client.base_url}. "
                f"Please ensure AdsPower is running and Local API is enabled."
            )

        # Load and allocate profiles
        profiles = await self.profile_manager.load_and_organize_profiles(
            group_name=self.config.adspower_group_name,
            active_count=self.config.active_profiles_count,
        )
        if not profiles:
            raise RuntimeError(
                f"No profiles found in AdsPower group '{self.config.adspower_group_name}'!"
            )

        # Calculate maximum workers needed across all shows in this run
        max_needed_workers = 1
        for s in shows:
            limit = s.max_per_order if s.max_per_order and s.max_per_order > 0 else 1
            needed_for_show = math.ceil(s.target_total / limit)
            if needed_for_show > max_needed_workers:
                max_needed_workers = needed_for_show

        profiles_count_to_start = min(max_needed_workers, self.config.active_profiles_count)
        LOGGER.info(
            f"Calculated maximum needed profiles for run: {profiles_count_to_start} "
            f"(target max: {max_needed_workers}, active pool limit: {self.config.active_profiles_count})"
        )

        # Initialize CDP Browser Pool with only needed profiles
        self.cdp_pool = CDPBrowserPool(
            config=self.config,
            client=self.client,
            profile_manager=self.profile_manager,
        )
        workers = await self.cdp_pool.initialize(count_needed=profiles_count_to_start)
        if not workers:
            raise RuntimeError("Failed to connect to any AdsPower browser workers via CDP!")

        # Setup RunContext / Checkpoint
        checkpoint_path = RunContext.find_last_active_checkpoint(self.config.runs_dir)
        if resume and checkpoint_path:
            ctx = RunContext.load_from_checkpoint(checkpoint_path, shows) or RunContext(shows)
        else:
            ctx = RunContext(shows)

        results: List[CheckResult] = []
        # Restore already done results if resumed
        for show in shows:
            if show.show_id in ctx.done_results:
                saved = ctx.done_results[show.show_id]
                res = CheckResult(
                    show_id=show.show_id,
                    name=saved.get("name", show.name),
                    url=show.url,
                    status=ShowStatus(saved.get("status", ShowStatus.OK.value)),
                    target=int(saved.get("target", show.target_total)),
                    reserved=int(saved.get("reserved", 0)),
                    available_approx=str(saved.get("available_approx", "")),
                    details=str(saved.get("details", "")),
                    notes=str(saved.get("notes", "")),
                )
                results.append(res)
                if on_show_done:
                    on_show_done(res, len(results), len(shows))

        try:
            # Process remaining shows
            pending_shows = [s for s in shows if s.show_id in ctx.pending_ids]
            LOGGER.info(f"Starting check for {len(pending_shows)} pending shows...")

            for show in pending_shows:
                ctx.mark_inflight(show.show_id)
                res = await self._check_single_show(show, workers)
                results.append(res)
                ctx.commit_done(res)

                if on_show_done:
                    on_show_done(res, len(results), len(shows))

            # Complete and write report
            ctx.complete_run()
            self.reporter.save_report(results)
            return results

        finally:
            if self.cdp_pool:
                await self.cdp_pool.close_all()

    async def _check_single_show(
        self,
        show: Show,
        workers: List[BrowserWorker],
    ) -> CheckResult:
        """Check a single event URL across the pool of browser workers."""
        LOGGER.info(f"--> Checking event '{show.name}' (Target: {show.target_total} tickets, Ticket index: {show.ticket_index})")

        # Step 1: Open show URL in primary worker
        primary_worker = workers[0]
        try:
            await primary_worker.page.goto(
                show.url,
                wait_until="domcontentloaded",
                timeout=self.config.nav_timeout,
            )
            await human_sleep((500, 1000))
            await accept_cookies_if_present(primary_worker.page)
            await close_blocking_popups(primary_worker.page)
            try:
                await primary_worker.page.wait_for_selector(
                    "select, button:has-text('Add Tickets'), input[value*='Add Tickets'], div[role='alert']",
                    timeout=15000,
                )
            except Exception:
                pass
        except Exception as exc:
            LOGGER.warning(f"Navigation failed for primary worker on {show.url}: {exc}")
            if self.detector.is_bad_proxy_error(exc) and self.cdp_pool:
                LOGGER.info("Attempting Hot-Swap for primary worker due to dead/timed out proxy...")
                new_primary = await self.cdp_pool.replace_worker_with_reserve(
                    primary_worker, reason=f"Primary nav error: {exc}"
                )
                if new_primary:
                    primary_worker = new_primary
                    try:
                        await primary_worker.page.goto(show.url, wait_until="domcontentloaded", timeout=self.config.nav_timeout)
                        await human_sleep((500, 1000))
                        await accept_cookies_if_present(primary_worker.page)
                        await close_blocking_popups(primary_worker.page)
                    except Exception as retry_exc:
                        LOGGER.error(f"Navigation failed again after primary hot-swap: {retry_exc}")
                        return CheckResult(
                            show_id=show.show_id,
                            name=show.name,
                            url=show.url,
                            status=ShowStatus.FAILED,
                            target=show.target_total,
                            reserved=0,
                            details=f"Ошибка навигации (прокси не отвечает): {retry_exc}",
                        )
            else:
                return CheckResult(
                    show_id=show.show_id,
                    name=show.name,
                    url=show.url,
                    status=ShowStatus.FAILED,
                    target=show.target_total,
                    reserved=0,
                    details=f"Ошибка навигации: {exc}",
                )

        # Step 2: Check SOLD OUT
        if await self.detector.is_soldout_page(primary_worker.page):
            screen = await self.reporter.save_screenshot(primary_worker.page, show.name, prefix="soldout")
            LOGGER.info(f"Event '{show.name}' is SOLD OUT.")
            return CheckResult(
                show_id=show.show_id,
                name=show.name,
                url=show.url,
                status=ShowStatus.SOLD_OUT,
                target=show.target_total,
                reserved=0,
                details="Билеты распроданы (SOLD OUT)",
                screenshot_path=screen,
            )

        # Step 3: Check Sales Ended
        if await self.detector.is_event_ended_page(primary_worker.page):
            screen = await self.reporter.save_screenshot(primary_worker.page, show.name, prefix="ended")
            LOGGER.info(f"Sales for event '{show.name}' have ENDED.")
            return CheckResult(
                show_id=show.show_id,
                name=show.name,
                url=show.url,
                status=ShowStatus.ENDED,
                target=show.target_total,
                reserved=0,
                details="Продажи завершены (Sales Ended)",
                screenshot_path=screen,
            )

        # Step 4: Check DataDome Block / Slider and execute 3-step recovery
        accessible_primary = await self._ensure_worker_accessible(primary_worker, show)
        if not accessible_primary:
            screen = await self.reporter.save_screenshot(primary_worker.page, show.name, prefix="blocked")
            return CheckResult(
                show_id=show.show_id,
                name=show.name,
                url=show.url,
                status=ShowStatus.BLOCKED,
                target=show.target_total,
                reserved=0,
                details="Заблокировано защитой DataDome (все 3 шага восстановления исчерпаны)",
                screenshot_path=screen,
            )
        primary_worker = accessible_primary

        # Step 5: Detect per-order limit
        detected_limit = await self.cart_handler.detect_per_order_limit(
            primary_worker.page, show.ticket_index
        )
        if show.max_per_order and show.max_per_order > 0:
            effective_max_per_order = min(show.max_per_order, detected_limit)
        else:
            effective_max_per_order = detected_limit
        if effective_max_per_order <= 0:
            effective_max_per_order = 1

        # Calculate required workers
        required_workers_count = min(math.ceil(show.target_total / effective_max_per_order), len(workers))
        other_workers = [w for w in workers if w != primary_worker]
        needed_others = max(0, required_workers_count - 1)
        selected_others = random.sample(other_workers, min(needed_others, len(other_workers)))
        selected_workers = [primary_worker] + selected_others

        LOGGER.info(
            f"Using {len(selected_workers)} workers for '{show.name}' "
            f"(Target: {show.target_total}, Max/Order: {effective_max_per_order}, Needed workers: {required_workers_count})"
        )

        # Step 6: Staggered URL navigation in remaining needed workers
        nav_tasks = []
        accumulated_nav_delay = 0.0
        nav_worker_list: List[BrowserWorker] = []
        for w in selected_workers:
            if w == primary_worker:
                continue
            accumulated_nav_delay += random.uniform(
                self.config.batch_nav_delay_ms[0], self.config.batch_nav_delay_ms[1]
            ) / 1000.0
            nav_worker_list.append(w)
            nav_tasks.append(
                self._open_and_prep_page(w.page, show.url, w.worker_index, delay_s=accumulated_nav_delay)
            )

        if nav_tasks:
            nav_results = await asyncio.gather(*nav_tasks, return_exceptions=True)
            for idx, res in enumerate(nav_results):
                w = nav_worker_list[idx]
                if isinstance(res, Exception) or res is False:
                    LOGGER.warning(f"[Worker #{w.worker_index}] Navigation prep returned error/false: {res}")

        # Verify each worker accessibility (slider/block/url) before adding to cart
        verified_workers: List[BrowserWorker] = []
        excluded_details: List[str] = []
        for w in selected_workers:
            ready_w = await self._ensure_worker_accessible(w, show)
            if ready_w:
                verified_workers.append(ready_w)
            else:
                LOGGER.warning(f"[Worker #{w.worker_index}] Excluded from cart addition due to unrecovered block or dead proxy.")
                excluded_details.append(f"[Worker #{w.worker_index}] Не удалось открыть/разблокировать страницу")

        selected_workers = verified_workers
        if not selected_workers:
            return CheckResult(
                show_id=show.show_id,
                name=show.name,
                url=show.url,
                status=ShowStatus.BLOCKED,
                target=show.target_total,
                reserved=0,
                details="Все задействованные профили заблокированы DataDome или недоступны.",
            )

        # Step 7: Staggered addition to cart across selected workers
        cart_tasks = []
        remaining_to_reserve = show.target_total
        accumulated_add_delay = 0.0
        active_cart_workers: List[BrowserWorker] = []

        for w in selected_workers:
            qty_for_worker = min(remaining_to_reserve, effective_max_per_order)
            if qty_for_worker <= 0:
                break
            active_cart_workers.append(w)
            cart_tasks.append(
                self._staggered_add_to_cart(
                    worker=w,
                    qty=qty_for_worker,
                    ticket_index=show.ticket_index,
                    delay_s=accumulated_add_delay,
                )
            )
            remaining_to_reserve -= qty_for_worker
            accumulated_add_delay += random.uniform(
                self.config.add_sequential_delay_ms[0], self.config.add_sequential_delay_ms[1]
            ) / 1000.0

        cart_results = await asyncio.gather(*cart_tasks, return_exceptions=True)

        total_reserved = 0
        success_workers: List[BrowserWorker] = []
        details_list = list(excluded_details)

        for idx, res in enumerate(cart_results):
            worker = active_cart_workers[idx]
            w_tag = f"Worker #{worker.worker_index}"
            if isinstance(res, tuple):
                ok, qty, msg = res
                if ok and qty > 0:
                    total_reserved += qty
                    success_workers.append(worker)
                    self.profile_manager.record_good_proxy(worker.profile.proxy_key)
                else:
                    details_list.append(f"[{w_tag}] {msg}")
            elif isinstance(res, Exception):
                details_list.append(f"[{w_tag}] Ошибка: {res}")
            else:
                details_list.append(f"[{w_tag}] Неизвестный результат: {res}")

        # Step 8: Calculate overall status
        if total_reserved >= show.target_total and not details_list:
            status = ShowStatus.OK
            details_str = f"Успешно зарезервировано {total_reserved}/{show.target_total}"
        elif total_reserved > 0:
            status = ShowStatus.PARTIAL
            details_str = f"Частично доступно: {total_reserved}/{show.target_total}."
            if details_list:
                details_str += " " + "; ".join(details_list)
        else:
            status = ShowStatus.INSUFFICIENT
            details_str = "Недостаточно билетов."
            if details_list:
                details_str += " " + "; ".join(details_list)

        LOGGER.info(
            f"Event '{show.name}' result: [{status.value}] Reserved: {total_reserved}/{show.target_total}"
        )

        # Step 9: Staggered release of carts after hold delay
        if total_reserved > 0:
            LOGGER.info(f"Holding reservations for {self.config.delay_before_clear_carts_s}s before clearing carts...")
            await asyncio.sleep(self.config.delay_before_clear_carts_s)

            clear_tasks = []
            accumulated_clear_delay = 0.0
            for w in success_workers:
                clear_tasks.append(
                    self._staggered_clear_cart(w, delay_s=accumulated_clear_delay)
                )
                accumulated_clear_delay += random.uniform(
                    self.config.clear_cart_stagger_ms[0], self.config.clear_cart_stagger_ms[1]
                ) / 1000.0

            await asyncio.gather(*clear_tasks, return_exceptions=True)
            LOGGER.info("All carts released successfully.")

        return CheckResult(
            show_id=show.show_id,
            name=show.name,
            url=show.url,
            status=status,
            target=show.target_total,
            reserved=total_reserved,
            available_approx=str(total_reserved) if total_reserved > 0 else "0",
            details=details_str,
        )

    async def _staggered_add_to_cart(
        self,
        worker: BrowserWorker,
        qty: int,
        ticket_index: Optional[int],
        delay_s: float,
    ) -> Tuple[bool, int, str]:
        """Wrapper to introduce incremental randomized offset before adding tickets."""
        if delay_s > 0:
            LOGGER.debug(f"[Worker #{worker.worker_index}] Waiting {delay_s:.2f}s stagger before Add Tickets...")
            await asyncio.sleep(delay_s)

        return await self.cart_handler.select_quantity_and_add(
            worker.page,
            requested_qty=qty,
            ticket_index=ticket_index,
        )

    async def _staggered_clear_cart(
        self,
        worker: BrowserWorker,
        delay_s: float,
    ) -> None:
        """Wrapper to introduce incremental randomized offset before clearing cart."""
        if delay_s > 0:
            LOGGER.debug(f"[Worker #{worker.worker_index}] Waiting {delay_s:.2f}s stagger before Clear Cart...")
            await asyncio.sleep(delay_s)

        await self.cart_handler.clear_cart(worker.page)

    async def _open_and_prep_page(
        self,
        page: Any,
        url: str,
        worker_index: int,
        delay_s: float = 0.0,
    ) -> bool:
        """Helper to navigate worker page to URL with incremental stagger."""
        try:
            if delay_s > 0:
                await asyncio.sleep(delay_s)
            else:
                await human_sleep(self.config.batch_nav_delay_ms)

            LOGGER.info(f"[Worker #{worker_index}] Opening URL: {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=self.config.nav_timeout)
            await accept_cookies_if_present(page)
            await close_blocking_popups(page)

            if await self.detector.is_bad_proxy_page(page):
                LOGGER.warning(f"[Worker #{worker_index}] Proxy connection failed: Chrome error page detected.")
                return False

            try:
                await page.wait_for_selector(
                    "select, [role='combobox'], .MuiSelect-select, button:has-text('Add Tickets'), input[value*='Add Tickets']",
                    timeout=12000,
                )
            except Exception:
                pass
            return True
        except Exception as exc:
            LOGGER.warning(f"[Worker #{worker_index}] Open URL failed ({exc})")
            return False

    async def _ensure_worker_accessible(
        self,
        worker: BrowserWorker,
        show: Show,
    ) -> Optional[BrowserWorker]:
        """
        Check if worker is facing DataDome slider, block, or dead proxy, and execute 3-step recovery:
        1. Humanized Drag & Drop of slider
        2. context.clear_cookies() + fresh tab reload
        3. Hot-Swap with reserve profile configured with clean good proxy
        """
        # Ensure page is actually on the target URL (not about:blank or error page)
        current_url = worker.page.url or ""
        if not current_url or current_url == "about:blank" or "chrome-error://" in current_url:
            LOGGER.info(f"[Worker #{worker.worker_index}] Page not loaded (url='{current_url}'). Loading {show.url}...")
            try:
                await worker.page.goto(show.url, wait_until="domcontentloaded", timeout=self.config.nav_timeout)
                await accept_cookies_if_present(worker.page)
                await close_blocking_popups(worker.page)
            except Exception as exc:
                LOGGER.warning(f"[Worker #{worker.worker_index}] Navigation attempt failed: {exc}")

        is_bad_proxy = await self.detector.is_bad_proxy_page(worker.page)
        is_blocked = await self.detector.is_blocked_page(worker.page)
        is_slider = await self.detector.is_slider_captcha(worker.page)

        if not is_blocked and not is_slider and not is_bad_proxy:
            # Check if page has ticket controls or matches target URL
            controls = await self.cart_handler.get_all_quantity_controls(worker.page)
            add_btn = await self.cart_handler.find_add_button(worker.page)
            if controls or add_btn or ("etix.com" in (worker.page.url or "")):
                return worker

        LOGGER.warning(
            f"[Worker #{worker.worker_index}] Challenge/Issue detected (slider={is_slider}, blocked={is_blocked}, bad_proxy={is_bad_proxy}). Starting recovery..."
        )

        # Stage 1: Fast Humanized Drag & Drop if slider is present
        if is_slider:
            LOGGER.info(f"[Worker #{worker.worker_index}] Step 1: Trying humanized slider drag...")
            solved = await solve_datadome_slider(worker.page)
            if solved and not await self.detector.is_blocked_page(worker.page) and not await self.detector.is_slider_captcha(worker.page):
                LOGGER.info(f"[Worker #{worker.worker_index}] Slider successfully solved on Step 1!")
                return worker

        # Stage 2: Clear cookies via CDP and reload in a fresh tab (if not dead proxy)
        if not is_bad_proxy:
            LOGGER.info(f"[Worker #{worker.worker_index}] Step 2: Clearing cookies and reopening in fresh tab...")
            try:
                await worker.context.clear_cookies()
                old_page = worker.page
                new_page = await worker.context.new_page()
                new_page.set_default_navigation_timeout(self.config.nav_timeout)
                new_page.set_default_timeout(self.config.click_timeout)
                worker.page = new_page
                try:
                    await old_page.close()
                except Exception:
                    pass

                await human_sleep((1000, 2000))
                await worker.page.goto(show.url, wait_until="domcontentloaded", timeout=self.config.nav_timeout)
                await accept_cookies_if_present(worker.page)
                await close_blocking_popups(worker.page)

                if not await self.detector.is_blocked_page(worker.page) and not await self.detector.is_slider_captcha(worker.page) and not await self.detector.is_bad_proxy_page(worker.page):
                    LOGGER.info(f"[Worker #{worker.worker_index}] Successfully unblocked on Step 2 (cookie reset)!")
                    return worker
            except Exception as exc:
                LOGGER.warning(f"[Worker #{worker.worker_index}] Step 2 error: {exc}")

        # Stage 3: Hot-Swap on reserve profile with clean good proxy
        LOGGER.info(f"[Worker #{worker.worker_index}] Step 3: Performing Hot-Swap to reserve profile with good proxy...")
        if self.cdp_pool:
            new_worker = await self.cdp_pool.replace_worker_with_reserve(
                failing_worker=worker,
                reason="DataDome / Bad proxy challenge unrecovered",
            )
            if new_worker:
                ok = await self._open_and_prep_page(new_worker.page, show.url, new_worker.worker_index)
                if ok and not await self.detector.is_blocked_page(new_worker.page) and not await self.detector.is_slider_captcha(new_worker.page):
                    LOGGER.info(f"[Worker #{new_worker.worker_index}] Hot-swap successful! Proceeding on new profile.")
                    return new_worker

        LOGGER.error(f"[Worker #{worker.worker_index}] All recovery steps exhausted.")
        return None
