"""Etix cart handler supporting both Material-UI custom comboboxes and standard select elements."""

from __future__ import annotations

import asyncio
import re
from typing import List, Optional, Tuple
from playwright.async_api import Page, Locator

from src.browser.human_actions import human_sleep
from src.config.settings import AppConfig
from src.etix.detector import EtixDetector
from src.utils.logger import LOGGER


class EtixCartHandler:
    """Handles ticket selection, cart addition, and cart cleanup for both MUI and classic Etix forms."""

    def __init__(self, config: AppConfig, detector: EtixDetector) -> None:
        self.config = config
        self.detector = detector

    async def get_all_quantity_controls(self, page: Page) -> List[Locator]:
        """
        Find all ticket quantity controls on page.
        Supports Material-UI comboboxes and standard select elements.
        """
        try:
            await page.wait_for_selector(
                "[role='combobox'], .MuiSelect-select, .smoketest-ticket-quantity, select",
                timeout=12000,
            )
        except Exception:
            pass

        # 1. Search for Material-UI comboboxes
        mui_combos = page.locator("[role='combobox'], .MuiSelect-select")
        count = await mui_combos.count()
        candidates: List[Locator] = []

        for i in range(count):
            loc = mui_combos.nth(i)
            try:
                if await loc.is_visible(timeout=500):
                    candidates.append(loc)
            except Exception:
                continue

        if candidates:
            return candidates

        # 2. Search for standard select tags
        selects = page.locator("select")
        count = await selects.count()
        for i in range(count):
            loc = selects.nth(i)
            try:
                if await loc.is_visible(timeout=500):
                    candidates.append(loc)
            except Exception:
                continue

        return candidates

    async def find_ticket_select(
        self,
        page: Page,
        ticket_index: Optional[int] = None,
    ) -> Optional[Locator]:
        """
        Find target ticket quantity selector.
        Supports 1-based ticket_index (1 = 1st ticket type, 2 = 2nd ticket type).
        """
        candidates = await self.get_all_quantity_controls(page)
        if not candidates:
            return None

        # 1-based indexing
        if ticket_index is not None and candidates:
            if ticket_index > 0:
                idx = ticket_index - 1
            else:
                idx = ticket_index  # negative index e.g. -1 for last

            if 0 <= idx < len(candidates):
                return candidates[idx]

            LOGGER.warning(
                f"ticket_index {ticket_index} out of bounds (found {len(candidates)}). Using index {len(candidates)-1}."
            )
            return candidates[-1]

        return candidates[0]

    async def detect_per_order_limit(
        self,
        page: Page,
        ticket_index: Optional[int] = None,
    ) -> int:
        """Detect max quantity per order from page text or options."""
        # 1. Check for text pattern on page: "Limit X tickets per order"
        try:
            body_text = await page.inner_text("body", timeout=1500)
            m = re.search(r"Limit\s+(\d+)\s+tickets?\s+per\s+order", body_text, re.I)
            if m:
                return int(m.group(1))
        except Exception:
            pass

        # 2. Check options of standard select if present
        sel = await self.find_ticket_select(page, ticket_index)
        if not sel:
            return 4

        try:
            tag_name = await sel.evaluate("el => el.tagName.toLowerCase()")
            if tag_name == "select":
                options = await sel.locator("option").all_inner_texts()
                nums = [int(o.strip()) for o in options if o.strip().isdigit()]
                if nums:
                    return max(nums)
        except Exception:
            pass

        return 4

    async def _select_mui_combobox_quantity(
        self,
        page: Page,
        combo: Locator,
        requested_qty: int,
    ) -> Tuple[bool, int]:
        """Click Material-UI combobox, wait for popover menu, and select quantity option."""
        try:
            await combo.scroll_into_view_if_needed(timeout=2000)
            await combo.click()
            await human_sleep((300, 600))

            # Wait for listbox options to appear
            await page.wait_for_selector("li[role='option'], .MuiMenuItem-root", timeout=4000)
            options = page.locator("li[role='option'], .MuiMenuItem-root")
            count = await options.count()
            if count == 0:
                return False, 0

            # Find matching option
            target_opt: Optional[Locator] = None
            opt_nums = []
            for i in range(count):
                opt = options.nth(i)
                txt = (await opt.inner_text()).strip()
                if txt.isdigit():
                    num = int(txt)
                    opt_nums.append((num, opt))

            if not opt_nums:
                return False, 0

            # Find exact match or closest <= requested_qty
            exact = next((opt for num, opt in opt_nums if num == requested_qty), None)
            if exact:
                await exact.click()
                await human_sleep((300, 500))
                return True, requested_qty

            # Pick largest available <= requested_qty or max available
            valid = [n for n, opt in opt_nums if n <= requested_qty and n > 0]
            chosen_num = max(valid) if valid else max(n for n, opt in opt_nums)
            chosen_opt = next(opt for num, opt in opt_nums if num == chosen_num)
            await chosen_opt.click()
            await human_sleep((300, 500))
            return True, chosen_num

        except Exception as exc:
            LOGGER.error(f"Error selecting MUI quantity: {exc}")
            return False, 0

    async def _robust_select_quantity(self, sel: Locator, qty: int) -> Tuple[bool, int]:
        """Select quantity on standard select element."""
        try:
            await sel.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            pass

        try:
            await sel.select_option(value=str(qty), timeout=2000)
            return True, qty
        except Exception:
            pass

        try:
            await sel.select_option(label=str(qty), timeout=2000)
            return True, qty
        except Exception:
            pass

        try:
            options = await sel.locator("option").all()
            texts = [(await o.inner_text()).strip() for o in options]
            available_nums = [int(t) for t in texts if t.isdigit()]
            if available_nums:
                target_qty = min(qty, max(available_nums))
                for idx, t in enumerate(texts):
                    if t.isdigit() and int(t) == target_qty:
                        await sel.evaluate(
                            "(el, i) => { el.selectedIndex = i; el.dispatchEvent(new Event('change', {bubbles: true})); }",
                            idx,
                        )
                        return True, target_qty
        except Exception:
            pass

        return False, 0

    async def find_add_button(self, page: Page) -> Optional[Locator]:
        """Find 'Add Tickets' or 'Add to Cart' button."""
        add_btn_selectors = [
            "button:has-text('Add Tickets')",
            "input[type='submit'][value*='Add Tickets']",
            "button:has-text('ADD TICKETS')",
            "button:has-text('Add to Cart')",
            "button[type='submit']:has-text('Add to Cart')",
            "input[type='submit'][value*='Add to Cart']",
            "button.btn-purchase",
            "button#purchase-btn",
            "button:has-text('Купить')",
            "button:has-text('Добавить в корзину')",
        ]

        for selector in add_btn_selectors:
            try:
                candidate = page.locator(selector).first
                if await candidate.is_visible(timeout=500):
                    return candidate
            except Exception:
                continue

        try:
            btn = page.get_by_role(
                "button",
                name=re.compile(r"ADD\s*TICKETS|ADD\s*TO\s*CART|КУПИТЬ", re.I),
            ).first
            if await btn.is_visible(timeout=500):
                return btn
        except Exception:
            pass

        return None

    async def select_quantity_and_add(
        self,
        page: Page,
        requested_qty: int,
        ticket_index: Optional[int] = None,
    ) -> Tuple[bool, int, str]:
        """
        Select ticket quantity, click Add to Cart, and wait for confirmation.
        Returns: (success, reserved_qty, status_message)
        """
        control = await self.find_ticket_select(page, ticket_index)
        if not control:
            return False, 0, "Dropdown/селектор выбора количества не найден"

        tag_name = await control.evaluate("el => el.tagName.toLowerCase()")
        if tag_name == "select":
            ok, selected_qty = await self._robust_select_quantity(control, requested_qty)
        else:
            # Material-UI custom combobox
            ok, selected_qty = await self._select_mui_combobox_quantity(page, control, requested_qty)

        if not ok or selected_qty == 0:
            return False, 0, "Не удалось выбрать количество в выпадающем списке"

        await human_sleep(self.config.after_click_sleep_ms)

        # Find and click Add button
        add_btn = await self.find_add_button(page)
        if not add_btn:
            return False, 0, "Кнопка 'Add Tickets / Add to Cart' не найдена"

        try:
            await add_btn.scroll_into_view_if_needed(timeout=2000)
            await add_btn.click(timeout=self.config.click_timeout)
        except Exception as exc:
            return False, 0, f"Ошибка клика 'Add Tickets': {exc}"

        # Wait for navigation or cart confirmation
        await human_sleep((1500, 3000))

        # Check for inventory exhaustion error message
        try:
            alert = page.locator(".alert-danger, .alert-warning, div[role='alert'], .error-message").first
            if await alert.is_visible(timeout=1000):
                alert_text = (await alert.inner_text()).strip()
                if self.detector.is_inventory_message(alert_text):
                    return False, 0, f"Лимит инвентаря: {alert_text}"
        except Exception:
            pass

        # Check if we arrived in cart / checkout
        if await self.detector.is_cart_page(page):
            return True, selected_qty, "Успешно добавлено в корзину"

        return True, selected_qty, f"Зарезервировано ({selected_qty} шт.)"

    async def clear_cart(self, page: Page) -> None:
        """Release tickets by clearing the shopping cart."""
        # Auto-accept JavaScript confirmation dialogs
        def handle_dialog(dialog):
            asyncio.create_task(dialog.accept())

        page.once("dialog", handle_dialog)

        # 1. Primary priority: 'Clear Shopping Cart' button/link
        bulk_clear_selectors = [
            "a:has-text('Clear Shopping Cart')",
            "button:has-text('Clear Shopping Cart')",
            "a:has-text('Clear Cart')",
            "button:has-text('Clear Cart')",
            "button:has-text('Empty Cart')",
            "a:has-text('Empty Cart')",
        ]

        for sel in bulk_clear_selectors:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=1000):
                    await btn.scroll_into_view_if_needed(timeout=1500)
                    await btn.click(timeout=2000)
                    await asyncio.sleep(1.0)

                    # Check for modal confirmation (e.g. Bootstrap / MUI modal "Are you sure?")
                    confirm_selectors = [
                        ".modal button:has-text('Yes')",
                        ".modal button:has-text('OK')",
                        ".modal button:has-text('Confirm')",
                        ".modal button:has-text('Clear')",
                        "button:has-text('Yes, clear cart')",
                    ]
                    for c_sel in confirm_selectors:
                        try:
                            c_btn = page.locator(c_sel).first
                            if await c_btn.is_visible(timeout=1000):
                                await c_btn.click(timeout=1500)
                                await asyncio.sleep(0.5)
                                break
                        except Exception:
                            continue

                    LOGGER.info("Cleared entire cart via 'Clear Shopping Cart'.")
                    return
            except Exception:
                continue

        # 2. Fallback: individual item Remove buttons/links
        item_remove_selectors = [
            "a:has-text('Remove')",
            "button:has-text('Remove')",
            "a.cart-remove",
            "button.cart-remove",
            "a:has-text('Delete')",
            "button:has-text('Delete')",
            "a[href*='remove']",
        ]

        for sel in item_remove_selectors:
            try:
                buttons = page.locator(sel)
                count = await buttons.count()
                for i in range(count):
                    btn = buttons.nth(i)
                    if await btn.is_visible(timeout=500):
                        await btn.click(timeout=1000)
                        await asyncio.sleep(0.3)
            except Exception:
                continue
