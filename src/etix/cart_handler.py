"""Etix cart handler for quantity selection, cart addition, and release."""

from __future__ import annotations

import asyncio
import re
from typing import Optional, Tuple
from playwright.async_api import Page, Locator

from src.browser.human_actions import human_sleep
from src.config.settings import AppConfig
from src.etix.detector import EtixDetector
from src.utils.logger import LOGGER


class EtixCartHandler:
    """Handles ticket selection, cart addition, and cart cleanup."""

    def __init__(self, config: AppConfig, detector: EtixDetector) -> None:
        self.config = config
        self.detector = detector

    async def find_ticket_select(
        self,
        page: Page,
        ticket_index: Optional[int] = None,
    ) -> Optional[Locator]:
        """
        Find the appropriate ticket quantity select dropdown.
        Supports 1-based ticket_index (e.g. 1 = 1st ticket type, 2 = 2nd ticket type).
        """
        # Wait up to 12s for select elements to be dynamically rendered by Etix JS
        try:
            await page.wait_for_selector("select", timeout=12000)
        except Exception:
            pass

        selects = page.locator("select")
        count = await selects.count()
        if count == 0:
            return None

        # Filter to visible select elements
        candidates = []
        for i in range(count):
            sel = selects.nth(i)
            try:
                if await sel.is_visible(timeout=500):
                    candidates.append(sel)
            except Exception:
                continue

        if not candidates:
            # Fallback to first select in DOM if visibility check was too strict
            return selects.first

        # Handle 1-based ticket_index (e.g. ticket_index=1 -> index 0, ticket_index=2 -> index 1)
        if ticket_index is not None and candidates:
            if ticket_index > 0:
                idx = ticket_index - 1
            else:
                idx = ticket_index  # e.g. -1 for last select

            if 0 <= idx < len(candidates):
                return candidates[idx]

            # If index is out of bounds, pick the closest available (e.g. last select)
            LOGGER.warning(
                f"ticket_index {ticket_index} exceeds available selects ({len(candidates)}). Using select at index {len(candidates)-1}."
            )
            return candidates[-1]

        return candidates[0]

    async def detect_per_order_limit(
        self,
        page: Page,
        ticket_index: Optional[int] = None,
    ) -> int:
        """Detect max quantity per order from select options."""
        sel = await self.find_ticket_select(page, ticket_index)
        if not sel:
            return 1
        try:
            options = await sel.locator("option").all_inner_texts()
            vals = []
            for opt in options:
                opt_str = opt.strip()
                if opt_str.isdigit():
                    vals.append(int(opt_str))
            if vals:
                return max(vals)
        except Exception:
            pass
        return 1

    async def _robust_select_quantity(self, sel: Locator, qty: int) -> Tuple[bool, int]:
        """Robustly select quantity dropdown with scrolling and multiple fallback methods."""
        try:
            await sel.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            pass

        # 1. Try standard select_option by value string
        try:
            await sel.select_option(value=str(qty), timeout=2000)
            val = (await sel.input_value()).strip()
            if val == str(qty):
                return True, qty
        except Exception:
            pass

        # 2. Try select_option by label string
        try:
            await sel.select_option(label=str(qty), timeout=2000)
            val = (await sel.input_value()).strip()
            if val == str(qty):
                return True, qty
        except Exception:
            pass

        # 3. Try finding closest available numeric option
        try:
            options = await sel.locator("option").all()
            texts = [(await o.inner_text()).strip() for o in options]
            available_nums = [int(t) for t in texts if t.isdigit()]

            if available_nums:
                target_qty = min(qty, max(available_nums))
                # Find matching option index
                for idx, t in enumerate(texts):
                    if t.isdigit() and int(t) == target_qty:
                        # JS evaluation for reliable selection trigger
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

        # Regex role search fallback
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
        sel = await self.find_ticket_select(page, ticket_index)
        if not sel:
            return False, 0, "Dropdown выбора количества не найден"

        # Select quantity
        ok, selected_qty = await self._robust_select_quantity(sel, requested_qty)
        if not ok or selected_qty == 0:
            return False, 0, "Не удалось выбрать количество в dropdown"

        await human_sleep(self.config.after_click_sleep_ms)

        # Find Add button
        add_btn = await self.find_add_button(page)
        if not add_btn:
            return False, 0, "Кнопка 'Add Tickets / Add to Cart' не найдена"

        try:
            await add_btn.scroll_into_view_if_needed(timeout=2000)
            await add_btn.click(timeout=self.config.click_timeout)
        except Exception as exc:
            return False, 0, f"Ошибка клика 'Add Tickets': {exc}"

        # Wait for navigation or inventory alert
        await human_sleep((1000, 2500))

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

        if "/cart" in page.url.lower() or "/checkout" in page.url.lower():
            return True, selected_qty, "В корзине"

        return True, selected_qty, f"Зарезервировано ({selected_qty} шт.)"

    async def clear_cart(self, page: Page) -> None:
        """Release tickets by clearing the shopping cart."""
        clear_selectors = [
            "button:has-text('Remove')",
            "a:has-text('Remove')",
            "button:has-text('Delete')",
            "a.cart-remove",
            "button.cart-remove",
            "button:has-text('Empty Cart')",
            "button:has-text('Очистить')",
            "button:has-text('Удалить')",
        ]
        for sel in clear_selectors:
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
