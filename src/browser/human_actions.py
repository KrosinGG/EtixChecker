"""Human interaction helpers, dialog dismissers, and DataDome slider solvers."""

from __future__ import annotations

import asyncio
import math
import random
from typing import List, Optional, Tuple
from playwright.async_api import Frame, Page, Locator

from src.utils.logger import LOGGER


async def human_sleep(delay_range_ms: Tuple[int, int]) -> None:
    """Sleep for a randomized duration within delay_range_ms."""
    lo, hi = delay_range_ms
    if hi < lo:
        lo, hi = hi, lo
    delay_ms = random.randint(lo, hi)
    await asyncio.sleep(delay_ms / 1000.0)


async def accept_cookies_if_present(page: Page, timeout_ms: int = 1500) -> None:
    """Detect and click common cookie consent buttons."""
    selectors = [
        "button#onetrust-accept-btn-handler",
        "button:has-text('Accept All Cookies')",
        "button:has-text('Accept Cookies')",
        "button:has-text('I Accept')",
        "button:has-text('Allow All')",
        "button:has-text('Принять')",
        "button:has-text('Согласен')",
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=timeout_ms):
                await btn.click(timeout=1000)
                await asyncio.sleep(0.3)
                LOGGER.debug(f"Accepted cookies via {sel}")
                return
        except Exception:
            continue


async def close_blocking_popups(page: Page, timeout_ms: int = 1500) -> None:
    """Close age verification, newsletter popups, or idle modals."""
    popup_selectors = [
        "button[aria-label='Close']",
        "button.close",
        ".modal-header button.close",
        ".modal-footer button:has-text('I am 21 or older')",
        "button:has-text('Continue to Event')",
        "button:has-text('Close')",
        "button:has-text('Закрыть')",
        "button:has-text('Yes, I am over 21')",
    ]
    for sel in popup_selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=timeout_ms):
                await btn.click(timeout=1000)
                await asyncio.sleep(0.3)
        except Exception:
            continue


def _generate_bezier_points(
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
    steps: int = 20,
) -> List[Tuple[float, float]]:
    """
    Generate humanized drag points with subtle curve acceleration and micro-jitter.
    """
    points = []
    # Control points with slight natural curvature
    ctrl1_x = start_x + (end_x - start_x) * random.uniform(0.25, 0.4)
    ctrl1_y = start_y + random.uniform(-4, 4)
    ctrl2_x = start_x + (end_x - start_x) * random.uniform(0.65, 0.85)
    ctrl2_y = end_y + random.uniform(-3, 3)

    for i in range(1, steps + 1):
        t = i / steps
        # Cubic Bezier formula
        inv = 1 - t
        x = (inv**3) * start_x + 3 * (inv**2) * t * ctrl1_x + 3 * inv * (t**2) * ctrl2_x + (t**3) * end_x
        y = (inv**3) * start_y + 3 * (inv**2) * t * ctrl1_y + 3 * inv * (t**2) * ctrl2_y + (t**3) * end_y
        # Add tiny jitter
        jitter_y = random.uniform(-0.8, 0.8) if i < steps else 0.0
        points.append((x, y + jitter_y))

    return points


async def solve_datadome_slider(page: Page, timeout_ms: int = 4000) -> bool:
    """
    Attempt to smoothly drag DataDome verification slider using humanized Bezier motion.
    Returns True if slider was moved and cleared, False otherwise.
    """
    LOGGER.info("Attempting humanized Drag & Drop on DataDome slider...")

    # Look for slider in main page or iframe frames
    slider_targets = [
        ".slider-button",
        "div[role='slider']",
        ".slider",
        ".geetest_slider_button",
        "#sec-slider-btn",
        ".sec-slider",
        ".captcha-slider-btn",
        "div.sliderBtn",
    ]

    track_targets = [
        ".sliderContainer",
        ".slider-track",
        ".track",
        ".sliderWrapper",
        "#sec-slider-track",
    ]

    target_handle = None
    target_frame: Optional[Frame] = None

    # Check main page
    for sel in slider_targets:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=500):
                target_handle = loc
                break
        except Exception:
            continue

    # Check iframes (DataDome uses captcha-delivery.com iframe)
    if not target_handle:
        for frame in page.frames:
            if "captcha" in frame.url.lower() or "datadome" in frame.url.lower():
                for sel in slider_targets:
                    try:
                        loc = frame.locator(sel).first
                        if await loc.is_visible(timeout=500):
                            target_handle = loc
                            target_frame = frame
                            break
                    except Exception:
                        continue
            if target_handle:
                break

    if not target_handle:
        LOGGER.warning("Could not find DataDome slider handle in page/frames.")
        return False

    try:
        box_handle = await target_handle.bounding_box()
        if not box_handle:
            return False

        # Attempt to find track width
        distance = 280.0  # default slider distance
        for t_sel in track_targets:
            try:
                scope = target_frame if target_frame else page
                t_loc = scope.locator(t_sel).first
                if await t_loc.is_visible(timeout=300):
                    box_track = await t_loc.bounding_box()
                    if box_track:
                        distance = max(180.0, box_track.width - box_handle.width - 5)
                        break
            except Exception:
                continue

        start_x = box_handle.x + box_handle.width / 2
        start_y = box_handle.y + box_handle.height / 2
        end_x = start_x + distance
        end_y = start_y + random.uniform(-2, 2)

        # Move mouse to start
        await page.mouse.move(start_x, start_y)
        await asyncio.sleep(random.uniform(0.1, 0.25))
        await page.mouse.down()
        await asyncio.sleep(random.uniform(0.05, 0.12))

        # Drag through curve points
        points = _generate_bezier_points(start_x, start_y, end_x, end_y, steps=random.randint(18, 26))
        for px, py in points:
            await page.mouse.move(px, py)
            await asyncio.sleep(random.uniform(0.008, 0.022))

        await asyncio.sleep(random.uniform(0.1, 0.2))
        await page.mouse.up()

        LOGGER.info("Completed slider drag motion. Awaiting validation...")
        await asyncio.sleep(2.5)

        # Check if slider disappeared
        try:
            if not await target_handle.is_visible(timeout=1000):
                LOGGER.info("DataDome slider solved successfully!")
                return True
        except Exception:
            return True

    except Exception as exc:
        LOGGER.warning(f"Failed to execute slider drag: {exc}")

    return False
