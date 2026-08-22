"""Main check engine coordinating AdsPower workers, Etix page checks, and cart allocations."""

from __future__ import annotations

import asyncio
import hashlib
import math
import random
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import pandas as pd

from src.adspower.client import AdsPowerClient
from src.adspower.profile_manager import AdsPowerProfileManager
from src.browser.cdp_pool import BrowserWorker, CDPBrowserPool
from src.browser.human_actions import accept_cookies_if_present, close_blocking_popups, human_sleep
from src.config.settings import AppConfig, CONFIG
from src.domain.enums import ProfileRole, ShowStatus
from src.domain.models import CheckResult, Show
from src.etix.cart_handler import EtixCartHandler
from src.etix.detector import EtixDetector
from src.storage.checkpoint import RunContext
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
            LOGGER.error(f"Navigation failed for primary worker on {show.url}: {exc}")
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

        # Step 4: Check DataDome Block
        if await self.detector.is_blocked_page(primary_worker.page):
            screen = await self.reporter.save_screenshot(primary_worker.page, show.name, prefix="blocked")
            LOGGER.warning(f"Worker #{primary_worker.worker_index} encountered DataDome block!")
            self.profile_manager.record_bad_proxy(primary_worker.profile.proxy_key, "DataDome block")
            return CheckResult(
                show_id=show.show_id,
                name=show.name,
                url=show.url,
                status=ShowStatus.BLOCKED,
                target=show.target_total,
                reserved=0,
                details="Заблокировано защитой (Access Temporarily Blocked)",
                screenshot_path=screen,
            )

        # Step 5: Detect per-order limit
        detected_limit = await self.cart_handler.detect_per_order_limit(
            primary_worker.page, show.ticket_index
        )
        max_per_order = min(show.max_per_order or detected_limit, detected_limit)
        if max_per_order <= 0:
            max_per_order = 1

        # Calculate required workers
        required_workers_count = min(math.ceil(show.target_total / max_per_order), len(workers))
        other_workers = [w for w in workers if w != primary_worker]
        needed_others = max(0, required_workers_count - 1)
        selected_others = random.sample(other_workers, min(needed_others, len(other_workers)))
        selected_workers = [primary_worker] + selected_others

        LOGGER.info(
            f"Using {len(selected_workers)} workers (Limit per order: {max_per_order}, Target: {show.target_total})"
        )

        # Step 6: Concurrently open URL in remaining needed workers
        nav_tasks = []
        for w in selected_workers:
            if w == primary_worker:
                continue
            nav_tasks.append(
                self._open_and_prep_page(w.page, show.url, w.worker_index)
            )

        if nav_tasks:
            await asyncio.gather(*nav_tasks, return_exceptions=True)

        # Step 7: Concurrently add tickets to cart across selected workers
        cart_tasks = []
        remaining_to_reserve = show.target_total

        for w in selected_workers:
            qty_for_worker = min(remaining_to_reserve, max_per_order)
            if qty_for_worker <= 0:
                break
            cart_tasks.append(
                self.cart_handler.select_quantity_and_add(
                    w.page,
                    requested_qty=qty_for_worker,
                    ticket_index=show.ticket_index,
                )
            )
            remaining_to_reserve -= qty_for_worker

        cart_results = await asyncio.gather(*cart_tasks, return_exceptions=True)

        total_reserved = 0
        success_workers: List[BrowserWorker] = []
        details_list = []

        for idx, res in enumerate(cart_results):
            worker = selected_workers[idx]
            if isinstance(res, tuple):
                ok, qty, msg = res
                if ok and qty > 0:
                    total_reserved += qty
                    success_workers.append(worker)
                    self.profile_manager.record_good_proxy(worker.profile.proxy_key)
                else:
                    details_list.append(f"[W#{worker.worker_index}] {msg}")
            else:
                details_list.append(f"[W#{worker.worker_index}] Error: {res}")

        # Step 8: Calculate overall status
        if total_reserved >= show.target_total:
            status = ShowStatus.OK
            details_str = f"Успешно зарезервировано {total_reserved}/{show.target_total}"
        elif total_reserved > 0:
            status = ShowStatus.PARTIAL
            details_str = f"Частично доступно: {total_reserved}/{show.target_total}. " + "; ".join(details_list)
        else:
            status = ShowStatus.INSUFFICIENT
            details_str = "Недостаточно билетов. " + "; ".join(details_list)

        LOGGER.info(
            f"Event '{show.name}' result: [{status.value}] Reserved: {total_reserved}/{show.target_total}"
        )

        # Step 9: Release carts after delay
        if total_reserved > 0:
            LOGGER.info(f"Waiting {self.config.delay_before_clear_carts_s}s before clearing carts...")
            await asyncio.sleep(self.config.delay_before_clear_carts_s)
            clear_tasks = [self.cart_handler.clear_cart(w.page) for w in success_workers]
            await asyncio.gather(*clear_tasks, return_exceptions=True)
            LOGGER.info("Carts released successfully.")

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

    async def _open_and_prep_page(self, page: Any, url: str, worker_index: int) -> bool:
        """Helper to navigate worker page to URL with jitter."""
        try:
            await human_sleep(self.config.batch_nav_delay_ms)
            await page.goto(url, wait_until="domcontentloaded", timeout=self.config.nav_timeout)
            await accept_cookies_if_present(page)
            await close_blocking_popups(page)
            try:
                await page.wait_for_selector(
                    "select, button:has-text('Add Tickets'), input[value*='Add Tickets']",
                    timeout=15000,
                )
            except Exception:
                pass
            return True
        except Exception as exc:
            LOGGER.warning(f"[Worker #{worker_index}] Open URL failed: {exc}")
            return False
