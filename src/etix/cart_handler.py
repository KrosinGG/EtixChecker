"""Etix cart handler for quantity selection, cart addition, and release."""

from __future__ import annotations

import asyncio
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
        """Find the appropriate ticket quantity select dropdown or input."""
        selects = page.locator("select[name*='quantity'], select[id*='quantity'], select.ticket-quantity")
        count = await selects.count()
        if count == 0:
            # Fallback generic select
            selects = page.locator("select")
            count = await selects.count()

        if count == 0:
            return None

        if ticket_index is not None and 0 <= ticket_index < count:
            return selects.nth(ticket_index)

        # Default to first visible select
        for i in range(count):
            candidate = selects.nth(i)
            if await candidate.is_visible(timeout=500):
                return candidate
        return selects.first

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

        # Determine available option to select
        try:
            options = await sel.locator("option").all_inner_texts()
            num_opts = [int(o.strip()) for o in options if o.strip().isdigit()]
            if not num_opts:
                return False, 0, "Нет доступных опций количества"

            max_opt = max(num_opts)
            target_qty = min(requested_qty, max_opt)

            # Select target quantity
            await sel.select_option(value=str(target_qty), timeout=3000)
            await human_sleep(self.config.after_click_sleep_ms)
        except Exception as exc:
            return False, 0, f"Ошибка выбора количества: {exc}"

        # Find Add to Cart button
        add_btn_selectors = [
            "button[type='submit']:has-text('Add to Cart')",
            "button:has-text('Add to Cart')",
            "input[type='submit'][value*='Add to Cart']",
            "button:has-text('Add Tickets')",
            "button.btn-purchase",
            "button#purchase-btn",
            "button:has-text('Купить')",
            "button:has-text('Добавить в корзину')",
        ]

        add_btn: Optional[Locator] = None
        for selector in add_btn_selectors:
            try:
                candidate = page.locator(selector).first
                if await candidate.is_visible(timeout=500):
                    add_btn = candidate
                    break
            except Exception:
                continue

        if not add_btn:
            return False, 0, "Кнопка 'Add to Cart' не найдена"

        try:
            await add_btn.click(timeout=self.config.click_timeout)
        except Exception as exc:
            return False, 0, f"Ошибка клика 'Add to Cart': {exc}"

        # Wait for navigation or alert
        await human_sleep((1000, 2000))

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
            return True, target_qty, "Успешно добавлено в корзину"

        # If URL contains cart or order summary is visible
        if "/cart" in page.url.lower():
            return True, target_qty, "В корзине"

        return True, target_qty, "Запрос отправлен (предположительно в корзине)"

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
