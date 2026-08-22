"""Detectors for Etix page states, sold out banners, DataDome challenges, and dead proxy errors."""

from __future__ import annotations

import re
from typing import Optional
from playwright.async_api import Page

from src.config.settings import AppConfig


class EtixDetector:
    """Detects Sold Out, Sales Ended, DataDome Blocked, Bad Proxy, and Inventory Error states."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def is_bad_proxy_error(self, exc: Exception) -> bool:
        """Check if exception was caused by expired, dead, or timed out proxy."""
        err_msg = str(exc).lower()
        bad_patterns = [
            "net::err_proxy_connection_failed",
            "net::err_connection_timed_out",
            "net::err_tunnel_connection_failed",
            "net::err_proxy_auth_requested",
            "net::err_timed_out",
            "net::err_connection_reset",
            "net::err_connection_refused",
            "net::err_connection_closed",
            "net::err_name_not_resolved",
            "net::err_empty_response",
            "timeout",
            "timeouterror",
            "proxy connection failed",
            "proxy authentication required",
            "connection closed",
            "connection reset",
        ]
        return any(p in err_msg for p in bad_patterns)

    async def is_bad_proxy_page(self, page: Page) -> bool:
        """Check if current page loaded Chrome network error or connection failure."""
        try:
            url = (page.url or "").lower()
            if "chrome-error://" in url or "about:error" in url:
                return True

            body_text = await page.inner_text("body", timeout=1000)
            error_phrases = [
                "err_proxy_connection_failed",
                "err_connection_timed_out",
                "err_tunnel_connection_failed",
                "this site can't be reached",
                "страница недоступна",
                "нет подключения к интернету",
                "no internet",
                "proxy error",
                "bad gateway",
                "502 bad gateway",
                "504 gateway time-out",
            ]
            return any(phrase in body_text.lower() for phrase in error_phrases)
        except Exception:
            return False

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
