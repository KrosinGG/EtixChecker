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
    steps: int = 32,
) -> List[Tuple[float, float]]:
    """
    Generate humanized drag points using a cubic Bezier curve with 3-phase kinematics:
    - Ease-In acceleration phase
    - Cruise phase with realistic micro-jitter on Y-axis (±1 - 3px)
    - Ease-Out deceleration phase near the target edge
    """
    points = []
    dx = end_x - start_x
    dy = end_y - start_y

    # Control points with slight natural curvature
    ctrl1_x = start_x + dx * random.uniform(0.22, 0.38)
    ctrl1_y = start_y + random.uniform(-4.0, 4.0)
    ctrl2_x = start_x + dx * random.uniform(0.65, 0.85)
    ctrl2_y = end_y + random.uniform(-3.0, 3.0)

    for i in range(1, steps + 1):
        raw_t = i / steps
        # Smoothstep / cubic easing for natural human acceleration and deceleration
        eased_t = (3 * (raw_t ** 2) - 2 * (raw_t ** 3)) if i < steps else 1.0

        inv = 1.0 - eased_t
        x = (inv ** 3) * start_x + 3 * (inv ** 2) * eased_t * ctrl1_x + 3 * inv * (eased_t ** 2) * ctrl2_x + (eased_t ** 3) * end_x
        y = (inv ** 3) * start_y + 3 * (inv ** 2) * eased_t * ctrl1_y + 3 * inv * (eased_t ** 2) * ctrl2_y + (eased_t ** 3) * end_y

        # Micro-tremor along Y axis: larger in the middle, dampened at start and end
        if 0.15 <= raw_t <= 0.88:
            jitter_y = random.uniform(-2.2, 2.2)
        elif raw_t < 0.15:
            jitter_y = random.uniform(-0.8, 0.8)
        else:
            jitter_y = random.uniform(-0.4, 0.4) if i < steps else 0.0

        points.append((x, y + jitter_y))

    return points


async def solve_datadome_slider(page: Page, timeout_ms: int = 5000) -> bool:
    """
    Detects and smoothly solves the DataDome slider challenge across all frames (main and iframes)
    using humanized Bezier mouse motion with bio-realistic hesitation pauses.
    Returns True if solved and verified, False otherwise.
    """
    LOGGER.info("Attempting humanized Drag & Drop on DataDome slider across all frames...")

    slider_targets = [
        "[role='slider']",
        ".slider-button",
        "#sec-slider",
        ".sec-slider-btn",
        "#sec-slider-button",
        ".slider",
        ".geetest_slider_button",
        "#sec-slider-btn",
        ".captcha-slider-btn",
        "div.sliderBtn",
        "#slider",
        ".tc-slider-normal",
        "div[class*='slider']",
        "div[class*='handle']",
        "button[class*='slider']",
    ]

    track_targets = [
        "#sec-slider-track",
        ".sliderContainer",
        ".slider-track",
        ".track",
        ".sliderWrapper",
        "div[class*='track']",
        "#track",
        ".slider-bg",
        "#sec-cpt-content",
    ]

    target_handle = None
    target_frame: Optional[Frame] = None

    # Step 1: Scan frames prioritizing captcha-delivery / datadome iframes
    frames_to_check: List[Optional[Frame]] = []
    for frame in page.frames:
        f_url = (frame.url or "").lower()
        if "captcha" in f_url or "datadome" in f_url:
            frames_to_check.insert(0, frame)
        else:
            frames_to_check.append(frame)

    # Also add main page scope (None means page level)
    scopes = frames_to_check + [None]

    for scope in scopes:
        context = scope if scope is not None else page
        for sel in slider_targets:
            try:
                loc = context.locator(sel).first
                if await loc.is_visible(timeout=300):
                    target_handle = loc
                    target_frame = scope
                    break
            except Exception:
                continue
        if target_handle:
            break

    # If still not found, check iframe elements explicitly
    if not target_handle:
        try:
            iframe_loc = page.locator("iframe[src*='captcha-delivery'], iframe[src*='datadome']").first
            if await iframe_loc.is_visible(timeout=500):
                c_frame = await iframe_loc.content_frame()
                if c_frame:
                    for sel in slider_targets:
                        try:
                            loc = c_frame.locator(sel).first
                            if await loc.is_visible(timeout=300):
                                target_handle = loc
                                target_frame = c_frame
                                break
                        except Exception:
                            continue
        except Exception:
            pass

    if not target_handle:
        LOGGER.warning("Could not find DataDome slider handle in any page/frames.")
        return False

    try:
        box_handle = await target_handle.bounding_box()
        if not box_handle or box_handle.get("width", 0) <= 0:
            LOGGER.warning("Found slider handle but bounding box is invalid.")
            return False

        # Find corresponding track element
        search_context = target_frame if target_frame is not None else page
        box_track = None
        for t_sel in track_targets:
            try:
                t_loc = search_context.locator(t_sel).first
                if await t_loc.is_visible(timeout=200):
                    candidate_box = await t_loc.bounding_box()
                    if candidate_box and candidate_box.get("width", 0) > box_handle["width"]:
                        box_track = candidate_box
                        break
            except Exception:
                continue

        start_x = box_handle["x"] + box_handle["width"] / 2.0
        start_y = box_handle["y"] + box_handle["height"] / 2.0

        if box_track:
            end_x = (box_track["x"] + box_track["width"]) - (box_handle["width"] / 2.0) - random.uniform(1.0, 3.0)
        else:
            # Fallback distance if track container width is not directly readable
            distance = random.uniform(270.0, 290.0)
            end_x = start_x + distance

        # Clamp end_x to viewport width
        vp = page.viewport_size or {"width": 1280, "height": 800}
        end_x = min(end_x, vp["width"] - 20)
        end_y = start_y + random.uniform(-1.5, 1.5)

        LOGGER.info(
            f"Slider drag plan: start=({start_x:.1f}, {start_y:.1f}) -> end=({end_x:.1f}, {end_y:.1f}) "
            f"[Distance: {end_x - start_x:.1f}px]"
        )

        # 1. Hover/Move to slider handle with natural human pre-positioning
        try:
            await target_handle.hover(timeout=1500)
        except Exception:
            await page.mouse.move(start_x, start_y)

        # 2. Pre-grab eye-hand reaction pause
        await asyncio.sleep(random.uniform(0.18, 0.32))

        # 3. Press left mouse button down
        await page.mouse.down()

        # 4. Post-grab hesitation delay before movement starts
        await asyncio.sleep(random.uniform(0.08, 0.16))

        # 5. Move along humanized Bezier curve
        steps = random.randint(28, 38)
        points = _generate_bezier_points(start_x, start_y, end_x, end_y, steps=steps)
        pause_milestone = random.randint(int(steps * 0.4), int(steps * 0.7))

        for idx, (px, py) in enumerate(points):
            await page.mouse.move(px, py)
            # Occasional micro-pause simulating motor coordination
            if idx == pause_milestone:
                await asyncio.sleep(random.uniform(0.015, 0.035))
            else:
                # Vary speed across curve
                phase_ratio = idx / steps
                if phase_ratio < 0.25 or phase_ratio > 0.8:
                    await asyncio.sleep(random.uniform(0.012, 0.024))
                else:
                    await asyncio.sleep(random.uniform(0.007, 0.016))

        # 6. End-of-drag human hold before releasing mouse button
        await asyncio.sleep(random.uniform(0.18, 0.28))

        # 7. Release mouse button
        await page.mouse.up()
        LOGGER.info("Completed slider drag motion. Awaiting DataDome validation...")

        # 8. Verification loop: wait up to 3.5s for challenge to resolve
        for _ in range(7):
            await asyncio.sleep(0.5)
            try:
                # Check if slider handle has vanished or hidden
                if not await target_handle.is_visible(timeout=300):
                    LOGGER.info("DataDome slider cleared successfully!")
                    return True
            except Exception:
                LOGGER.info("DataDome slider handle detached / solved!")
                return True

            # Check if datadome cookie is now present in browser context
            try:
                cookies = await page.context.cookies()
                dd_cookies = [c for c in cookies if "datadome" in c.get("name", "").lower()]
                if dd_cookies and any(len(c.get("value", "")) > 20 for c in dd_cookies):
                    LOGGER.info(f"Found active DataDome session cookie: {dd_cookies[0]['name']}")
                    return True
            except Exception:
                pass

        # Final check if slider is still visible
        try:
            if not await target_handle.is_visible(timeout=500):
                LOGGER.info("DataDome slider solved!")
                return True
        except Exception:
            return True

    except Exception as exc:
        LOGGER.warning(f"Failed to execute slider drag: {exc}")

    return False
