"""Detectors for Etix page states, sold out banners, and DataDome security challenges."""

from __future__ import annotations

import re
from typing import Optional
from playwright.async_api import Page

from src.config.settings import AppConfig


class EtixDetector:
    """Detects Sold Out, Sales Ended, DataDome Blocked, and Inventory Error states."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    async def is_soldout_page(self, page: Page) -> bool:
        """Check whether the page indicates the event is Sold Out."""
        for selector in self.config.sold_out_banner_selectors:
            try:
                locator = page.locator(selector).first
                if await locator.is_visible(timeout=500):
                    return True
            except Exception:
                continue

        try:
            body_text = await page.inner_text("body", timeout=1500)
            for pattern in self.config.sold_out_text_patterns:
                if re.search(pattern, body_text, flags=re.I):
                    return True
        except Exception:
            pass

        return False

    async def is_event_ended_page(self, page: Page) -> bool:
        """Check whether sales for this event have ended."""
        for selector in self.config.ended_selectors:
            try:
                locator = page.locator(selector).first
                if await locator.is_visible(timeout=500):
                    return True
            except Exception:
                continue

        try:
            body_text = await page.inner_text("body", timeout=1500)
            for pattern in self.config.ended_text_patterns:
                if re.search(pattern, body_text, flags=re.I):
                    return True
        except Exception:
            pass

        return False

    async def is_blocked_page(self, page: Page) -> bool:
        """Check whether DataDome returned 'Access Temporarily Blocked'."""
        try:
            body_text = await page.inner_text("body", timeout=1500)
            for pattern in self.config.blocked_text_patterns:
                if re.search(pattern, body_text, flags=re.I):
                    return True
        except Exception:
            pass
        return False

    async def is_slider_captcha(self, page: Page) -> bool:
        """Check whether DataDome displayed the 'Slide right to secure your access' slider."""
        try:
            body_text = await page.inner_text("body", timeout=1500)
            for pattern in self.config.slider_captcha_patterns:
                if re.search(pattern, body_text, flags=re.I):
                    return True
        except Exception:
            pass
        return False

    def is_inventory_message(self, text: str) -> bool:
        """Check whether an alert text indicates inventory exhaustion / insufficient tickets."""
        for pattern in self.config.inventory_error_patterns:
            if re.search(pattern, text, flags=re.I):
                return True
        return False

    async def is_cart_page(self, page: Page) -> bool:
        """Check whether page navigated to Shopping Cart / Review step."""
        url = page.url.lower()
        cart_keywords = ["cart", "shoppingcart", "viewshoppingcart", "checkout", "review", "basket"]
        if any(k in url for k in cart_keywords):
            return True
        try:
            has_cart_elem = await page.locator(
                ".cart-item, #cart-container, .order-summary, table.cart, #shopping-cart, .shoppingCart"
            ).first.is_visible(timeout=500)
            return bool(has_cart_elem)
        except Exception:
            return False
