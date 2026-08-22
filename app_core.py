import asyncio
import random
import re
import time
import math
import os
import json
import hashlib
import traceback
from urllib.parse import urlparse, parse_qsl, urlunparse, urlencode
from pathlib import Path
from typing import List, Optional, Dict, Tuple, Callable, Awaitable

import pandas as pd
import yaml
from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    Locator,
)

from app_logging import init_logger
from captcha_solver import (
    CaptchaSolverConfig,
    TwoCaptchaFunCaptchaSolver,
    apply_funcaptcha_token,
    extract_funcaptcha_task,
    solve_visual_funcaptcha_via_coordinates,
    try_reload_recaptcha,
)
from config import load_config

CONFIG = load_config()

DATA_DIR = Path("data")
PROXIES_CSV = DATA_DIR / "Chezile.csv"
SHOWS_CSV = DATA_DIR / "shows.csv"
RUNS_ROOT = Path("runs")
APP_VERSION = "1.0.0"

HEADLESS: bool = CONFIG.headless
RANDOMIZE_PROXIES: bool = CONFIG.randomize_proxies
SLOWMO_MS = CONFIG.slowmo_ms
TABS_COUNT = CONFIG.tabs_count

NAV_TIMEOUT = CONFIG.nav_timeout
CLICK_TIMEOUT = CONFIG.click_timeout
BATCH_NAV_DELAY = CONFIG.batch_nav_delay_ms
AFTER_CLICK_SLEEP = CONFIG.after_click_sleep_ms
ADD_SEQUENTIAL_DELAY = CONFIG.add_sequential_delay_ms
DELAY_BEFORE_CLEAR_CARTS_S = CONFIG.delay_before_clear_carts_s

SCREENS_DIR = Path("screens")
LOGS_DIR = Path("logs")
STRICT_ALL_CARTS = CONFIG.strict_all_carts

LOGGER = init_logger(LOGS_DIR)

MANUAL_INSUFFICIENT_MARKER = "MANUAL_INSUFFICIENT"
MANUAL_TAB_LIMIT = 15

def _as_int(value: object, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default

def _as_float(value: object, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default

def _as_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return default

def _as_range(value: object, default: Tuple[int, int]) -> Tuple[int, int]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            lo = int(value[0])
            hi = int(value[1])
            if hi < lo:
                lo, hi = hi, lo
            return (lo, hi)
        except Exception:
            return default
    if isinstance(value, str) and "," in value:
        parts = [p.strip() for p in value.split(",") if p.strip()]
        if len(parts) == 2:
            try:
                lo = int(parts[0])
                hi = int(parts[1])
                if hi < lo:
                    lo, hi = hi, lo
                return (lo, hi)
            except Exception:
                return default
    return default

def apply_runtime_profile(profile: dict) -> None:
    global HEADLESS, RANDOMIZE_PROXIES, SLOWMO_MS, TABS_COUNT
    global NAV_TIMEOUT, CLICK_TIMEOUT, BATCH_NAV_DELAY, AFTER_CLICK_SLEEP
    global ADD_SEQUENTIAL_DELAY, DELAY_BEFORE_CLEAR_CARTS_S, STRICT_ALL_CARTS

    HEADLESS = _as_bool(profile.get("headless"), HEADLESS)
    RANDOMIZE_PROXIES = _as_bool(profile.get("randomize_proxies"), RANDOMIZE_PROXIES)
    SLOWMO_MS = _as_int(profile.get("slowmo_ms"), SLOWMO_MS)
    TABS_COUNT = max(1, _as_int(profile.get("tabs_count"), TABS_COUNT))
    NAV_TIMEOUT = _as_int(profile.get("nav_timeout"), NAV_TIMEOUT)
    CLICK_TIMEOUT = _as_int(profile.get("click_timeout"), CLICK_TIMEOUT)
    BATCH_NAV_DELAY = _as_range(profile.get("batch_nav_delay_ms"), BATCH_NAV_DELAY)
    AFTER_CLICK_SLEEP = _as_range(profile.get("after_click_sleep_ms"), AFTER_CLICK_SLEEP)
    ADD_SEQUENTIAL_DELAY = _as_range(profile.get("add_sequential_delay_ms"), ADD_SEQUENTIAL_DELAY)
    DELAY_BEFORE_CLEAR_CARTS_S = _as_float(
        profile.get("delay_before_clear_carts_s"), DELAY_BEFORE_CLEAR_CARTS_S
    )
    STRICT_ALL_CARTS = _as_bool(profile.get("strict_all_carts"), STRICT_ALL_CARTS)

ERROR_PATTERNS = [
    r"not enough tickets",
    r"not enough adjacent seats",
    r"not enough tickets of that type available",
    r"change the type of tickets you are requesting",
    r"reduce the number of tickets and try again",
    r"\bPlease\s+reduce\b",
    r"\bChoose\s+fewer\b",
    r"Выберите меньше",
    r"уменьш",
]

CAPTCHA_IFRAME_SELECTORS = CONFIG.captcha_iframe_selectors
CAPTCHA_ELEMENT_SELECTORS = CONFIG.captcha_element_selectors
CAPTCHA_TEXT_PATTERNS_EXTRA = [
    r"\bЯ\s+не\s+робот\b",
    r"выберите все изображения",
    r"виберіть усі зображення",
    r"позначте всі зображення",
]
CAPTCHA_TEXT_PATTERNS = CONFIG.captcha_text_patterns + CAPTCHA_TEXT_PATTERNS_EXTRA

BEGIN_CAPTCHA_TEXT_PATTERNS = [
    r"confirm you are human",
    r"security check",
    r"verifies that you are not a bot",
    r"prevent spam",
]

SOLD_OUT_EMAIL_TEXT = CONFIG.sold_out_email_text
SOLD_OUT_BANNER_SELECTORS = CONFIG.sold_out_banner_selectors
SOLD_OUT_TEXT_PATTERNS = CONFIG.sold_out_text_patterns

ENDED_SELECTORS = CONFIG.ended_selectors
ENDED_TEXT_PATTERNS = CONFIG.ended_text_patterns

def is_inventory_message(text: str) -> bool:
    if not text:
        return False
    if "уменьш" in text.lower():
        return True
    return any(re.search(p, text, flags=re.I) for p in ERROR_PATTERNS)

def is_select_qty_prompt(text: str) -> bool:
    if not text:
        return False
    return bool(re.search(r"Please\s+select\s+one\s+or\s+more\s+tickets", text, flags=re.I))

def is_cart_url(url: str) -> bool:
    if not url:
        return False
    return bool(re.search(r"/(cart|basket)(/|$)", url, flags=re.I))

async def is_cart_page(page: Page) -> bool:
    try:
        return is_cart_url(page.url or "")
    except Exception:
        return False

async def _any_visible(locator) -> bool:
    try:
        count = await locator.count()
        for i in range(count):
            if await locator.nth(i).is_visible():
                return True
    except Exception:
        return False
    return False


def _is_target_closed_exception(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "target page, context or browser has been closed" in text
        or "page has been closed" in text
        or "page crashed" in text
    )


async def has_ga_purchase_controls(page: Page) -> bool:
    sel = await find_ticket_select(page, preferred_index=None)
    if not sel or not await sel.is_visible():
        return False

    add_btn = page.get_by_role("button", name=re.compile(r"ADD\s*TICKETS", re.I)).first
    if await add_btn.count() == 0:
        add_btn = page.locator("input[type='submit'][value*='ADD TICKETS']").first

    return await _any_visible(add_btn)

async def is_captcha(page: Page) -> bool:
    """
    Heuristically detect CAPTCHA widgets (reCAPTCHA/hCaptcha/etc.)
    on the current page. Never raises, returns False on any error.
    """
    try:
        if page.is_closed():
            return False
        iframe_loc = page.locator(", ".join(CAPTCHA_IFRAME_SELECTORS))
        if await _any_visible(iframe_loc):
            return True

        if await _any_visible(page.locator(", ".join(CAPTCHA_ELEMENT_SELECTORS))):
            return True

        body_text = ""
        try:
            body_text = await page.locator("body").inner_text()
        except Exception:
            body_text = ""

        if not body_text:
            return False

        for pat in CAPTCHA_TEXT_PATTERNS:
            if re.search(pat, body_text, flags=re.I):
                if re.search(r"\b(recaptcha|hcaptcha|captcha)\b", body_text, flags=re.I):
                    return True
                log_line(
                    LOGS_DIR / "network_health.log",
                    f"[{time.strftime('%H:%M:%S')}] CAPTCHA text-only match; url={page.url}",
                )
                return False
        if await is_begin_captcha(page, body_text=body_text):
            return True
        return False
    except Exception as exc:
        if _is_target_closed_exception(exc):
            return False
        log_exception("is_captcha")
        return False

async def is_begin_captcha(page: Page, body_text: Optional[str] = None) -> bool:
    """
    Detects "Let's confirm you are human" page with Begin button.
    """
    try:
        if page.is_closed():
            return False
        btn = page.get_by_role("button", name=re.compile(r"^\s*Begin\s*$", re.I)).first
        if await btn.count() == 0:
            btn = page.locator(
                "button:has-text('Begin'), input[type='submit'][value*='Begin'], a:has-text('Begin')"
            ).first
        if await btn.count() == 0 or not await btn.is_visible():
            return False

        heading = page.get_by_role(
            "heading", name=re.compile(r"confirm you are human", re.I)
        ).first
        if await heading.count() > 0 and await heading.is_visible():
            return True

        if body_text is None:
            try:
                body_text = await page.locator("body").inner_text()
            except Exception:
                body_text = ""
        if body_text and any(re.search(p, body_text, flags=re.I) for p in BEGIN_CAPTCHA_TEXT_PATTERNS):
            return True

        try:
            title = await page.title()
        except Exception:
            title = ""
        if title and re.search(r"human|security", title, flags=re.I):
            return True

        try:
            url_now = page.url or ""
        except Exception:
            url_now = ""
        if re.search(r"captcha|human|security", url_now, flags=re.I):
            return True

        return False
    except Exception as exc:
        if _is_target_closed_exception(exc):
            return False
        log_exception("is_begin_captcha")
        return False


async def click_begin_captcha(page: Page) -> bool:
    """
    Click Begin button on the pre-captcha page, if present.
    """
    try:
        btn = page.get_by_role("button", name=re.compile(r"^\s*Begin\s*$", re.I)).first
        if await btn.count() == 0:
            btn = page.locator(
                "button:has-text('Begin'), input[type='submit'][value*='Begin'], a:has-text('Begin')"
            ).first
        if await btn.count() == 0 or not await btn.is_visible():
            return False
        await btn.scroll_into_view_if_needed()
        await btn.click(timeout=5000)
        return True
    except Exception:
        log_exception("click_begin_captcha")
        return False

async def is_ended_reliable(page: Page) -> bool:
    try:
        if await has_ga_purchase_controls(page):
            return False
    except Exception:
        return False
    return await is_event_ended_page(page)

async def is_soldout_page(page: Page) -> bool:
    deadline = time.monotonic() + 2.5
    while time.monotonic() < deadline:
        try:
            sel = await find_ticket_select(page, preferred_index=None)
            add_btn_visible = (
                await _any_visible(page.get_by_role("button", name=re.compile(r"ADD\s*TICKETS", re.I)))
                or await _any_visible(page.locator("input[type='submit'][value*='ADD TICKETS']"))
            )
            has_controls = bool(sel) and await sel.is_visible() and add_btn_visible
            if has_controls:
                return False

            alert_widget = await _any_visible(
                page.locator(
                    f":is(div, section, form):has-text(\"{SOLD_OUT_EMAIL_TEXT}\")"
                )
            )
            if alert_widget:
                return True

            banner = await _any_visible(
                page.locator(", ".join(SOLD_OUT_BANNER_SELECTORS))
            )
            if banner:
                return True

            try:
                body = await page.locator("body").inner_text()
            except Exception:
                body = ""
            if body and any(re.search(p, body, flags=re.I) for p in SOLD_OUT_TEXT_PATTERNS):
                log_line(
                    LOGS_DIR / "network_health.log",
                    f"[{time.strftime('%H:%M:%S')}] SOLD OUT text-only match; url={page.url}",
                )
        except Exception:
            pass

        await page.wait_for_timeout(200)

    return False

async def is_event_ended_page(page: Page) -> bool:
    for sel in ENDED_SELECTORS:
        try:
            if await page.locator(sel).first.is_visible():
                return True
        except Exception:
            pass

    try:
        body = await page.locator("body").inner_text()
    except Exception:
        body = ""

    if any(re.search(p, body, flags=re.I) for p in ENDED_TEXT_PATTERNS):
        log_line(
            LOGS_DIR / "network_health.log",
            f"[{time.strftime('%H:%M:%S')}] ENDED text-only match; url={page.url}",
        )
        return True
    return False

USER_AGENTS: List[str] = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/127.0.0.0 Safari/537.36"
    ),
]

VIEWPORT_PRESETS: List[Dict[str, int]] = [
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1600, "height": 900},
    {"width": 1920, "height": 1080},
]

US_GEO_PROFILES: List[Dict[str, float | str]] = [
    {"city": "New York", "timezone_id": "America/New_York", "lat": 40.7128, "lon": -74.0060},
    {"city": "Chicago", "timezone_id": "America/Chicago", "lat": 41.8781, "lon": -87.6298},
    {"city": "Denver", "timezone_id": "America/Denver", "lat": 39.7392, "lon": -104.9903},
    {"city": "Los Angeles", "timezone_id": "America/Los_Angeles", "lat": 34.0522, "lon": -118.2437},
    {"city": "Seattle", "timezone_id": "America/Los_Angeles", "lat": 47.6062, "lon": -122.3321},
    {"city": "Miami", "timezone_id": "America/New_York", "lat": 25.7617, "lon": -80.1918},
    {"city": "Atlanta", "timezone_id": "America/New_York", "lat": 33.7490, "lon": -84.3880},
    {"city": "Dallas", "timezone_id": "America/Chicago", "lat": 32.7767, "lon": -96.7970},
]


def pick_user_agent() -> str:
    return random.choice(USER_AGENTS)


def pick_viewport() -> Dict[str, int]:
    return random.choice(VIEWPORT_PRESETS)


def pick_geo_profile() -> Dict[str, float | str]:
    return random.choice(US_GEO_PROFILES)


def ensure_dirs():
    SCREENS_DIR.mkdir(exist_ok=True)
    LOGS_DIR.mkdir(exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)
    RUNS_ROOT.mkdir(exist_ok=True)


def log_line(fname: Path, text: str):
    try:
        fname.parent.mkdir(parents=True, exist_ok=True)
        with fname.open("a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception:
        log_exception(f"log_line.write:{fname}")
        return
    try:
        LOGGER.info("%s %s", fname.name, text)
    except Exception:
        log_debug("clear_cart failed")

def log_exception(context: str) -> None:
    try:
        tb = traceback.format_exc()
        if tb.strip() == "NoneType: None":
            LOGGER.error("%s: (no exception)", context)
        else:
            LOGGER.error("%s: %s", context, tb)
    except Exception:
        pass

def log_debug(context: str) -> None:
    try:
        LOGGER.debug("%s", context)
    except Exception:
        pass

HUMAN_CONFIG_PATH = DATA_DIR / "human.yml"

DEFAULT_HUMAN_CONFIG: dict = {
    "stagger_batch_range": [6, 8],
    "stagger_pause_sec": [0.8, 1.5],
    "think_times_ms": {
        "pre_click_add_to_cart": [350, 900],
        "post_click_wait": [250, 600],
        "between_navigations_ms": [800, 2100],
    },
    "humanize_level": 1.0,
    "enable_mouse_move": True,
    "domain_rate_limit_per_sec": 6.0,
}


def _deep_update_dict(base: dict, updates: dict) -> dict:
    """
    Recursively merge dictionaries, returning a new dict.
    """
    result = dict(base)
    for key, value in updates.items():
        if (
            isinstance(value, dict)
            and isinstance(result.get(key), dict)
        ):
            result[key] = _deep_update_dict(result[key], value)
        else:
            result[key] = value
    return result


def load_or_create_human_config(path: Path) -> dict:
    """
    Load human-behaviour config from YAML or create with defaults.

    If file is missing or invalid, DEFAULT_HUMAN_CONFIG is used and
    written back to disk once.
    """
    cfg: dict = dict(DEFAULT_HUMAN_CONFIG)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            try:
                raw = path.read_text(encoding="utf-8")
                data = yaml.safe_load(raw) or {}
                if isinstance(data, dict):
                    cfg = _deep_update_dict(DEFAULT_HUMAN_CONFIG, data)
            except Exception:
                log_exception("load_or_create_human_config.read")
        else:
            try:
                with path.open("w", encoding="utf-8") as f:
                    yaml.safe_dump(
                        DEFAULT_HUMAN_CONFIG,
                        f,
                        sort_keys=False,
                        allow_unicode=True,
                    )
            except Exception:
                log_exception("load_or_create_human_config.write_default")
    except Exception:
        log_exception("load_or_create_human_config")
    return cfg


class HumanScheduler:
    """
    Human-like timing and rate limiting helper.

    - sleep_jitter* — задержки с джиттером и глобальным множителем.
    - build_batches — нарезка вкладок на волны (stagger).
    - pause_between_batches — пауза между волнами.
    - rate_limit — мягкий лимитер действий по домену.
    - move_mouse_to — плавное подведение курсора к элементу.
    """

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg or {}

        self.stagger_batch_range = self._normalize_range(
            self.cfg.get("stagger_batch_range"), (6.0, 8.0)
        )
        self.stagger_pause_sec_range = self._normalize_range(
            self.cfg.get("stagger_pause_sec"), (0.8, 1.5)
        )

        think = self.cfg.get("think_times_ms") or {}
        self.pre_click_range_ms = self._normalize_range(
            think.get("pre_click_add_to_cart"), (350.0, 900.0)
        )
        self.post_click_range_ms = self._normalize_range(
            think.get("post_click_wait"), (250.0, 600.0)
        )
        self.between_nav_range_ms = self._normalize_range(
            think.get("between_navigations_ms"), (800.0, 2100.0)
        )

        try:
            self.humanize_level = float(self.cfg.get("humanize_level", 1.0))
        except Exception:
            self.humanize_level = 1.0
        self.humanize_level = max(0.0, self.humanize_level)

        self.enable_mouse_move = bool(self.cfg.get("enable_mouse_move", True))

        try:
            self.domain_rate_limit_per_sec = float(
                self.cfg.get("domain_rate_limit_per_sec", 6.0)
            )
        except Exception:
            self.domain_rate_limit_per_sec = 6.0
        if self.domain_rate_limit_per_sec < 0:
            self.domain_rate_limit_per_sec = 0.0

        self._domain_last_action: Dict[str, float] = {}
        self._rate_lock = asyncio.Lock()

    @staticmethod
    def _normalize_range(
        value: Optional[object], default: Tuple[float, float]
    ) -> Tuple[float, float]:
        try:
            if isinstance(value, (list, tuple)) and len(value) == 2:
                lo = float(value[0])
                hi = float(value[1])
                if hi < lo:
                    lo, hi = hi, lo
                return lo, hi
        except Exception:
            pass
        return default

    def _scaled_range_ms(self, base: Tuple[float, float]) -> Tuple[float, float]:
        level = self.humanize_level
        return base[0] * level, base[1] * level

    def _scaled_range_sec(self, base: Tuple[float, float]) -> Tuple[float, float]:
        level = self.humanize_level
        return base[0] * level, base[1] * level

    async def sleep_ms_range(self, base_ms_range: Tuple[float, float]) -> None:
        """
        Sleep for a random time within the scaled millisecond range.
        """
        try:
            lo, hi = self._scaled_range_ms(base_ms_range)
            if hi <= 0 or lo < 0:
                return
            delay = random.uniform(lo, hi) / 1000.0
            await asyncio.sleep(delay)
        except Exception:
            log_exception("HumanScheduler.sleep_ms_range")

    async def sleep_pre_click_add_to_cart(self) -> None:
        await self.sleep_ms_range(self.pre_click_range_ms)

    async def sleep_post_click_wait(self) -> None:
        await self.sleep_ms_range(self.post_click_range_ms)

    async def sleep_between_navigations(self) -> None:
        await self.sleep_ms_range(self.between_nav_range_ms)

    def build_batches(self, total_tabs: int) -> List[List[int]]:
        """
        Split tab indices into staggered batches.
        """
        indices = list(range(total_tabs))
        if total_tabs <= 0:
            return []
        lo, hi = self.stagger_batch_range
        if lo <= 0 or hi <= 0:
            return [indices]

        batches: List[List[int]] = []
        pos = 0
        while pos < total_tabs:
            try:
                size = random.randint(int(lo), int(hi))
            except ValueError:
                size = max(1, int(hi))
            if size <= 0:
                size = 1
            batch = indices[pos : pos + size]
            batches.append(batch)
            pos += size
        return batches

    async def pause_between_batches(self) -> None:
        """
        Pause between stagger batches based on config.
        """
        try:
            lo, hi = self._scaled_range_sec(self.stagger_pause_sec_range)
            if hi <= 0 or lo < 0:
                return
            delay = random.uniform(lo, hi)
            await asyncio.sleep(delay)
        except Exception:
            log_exception("HumanScheduler.pause_between_batches")

    async def rate_limit(self, url: str) -> None:
        if self.domain_rate_limit_per_sec <= 0:
            return
        try:
            parsed = urlparse(url)
            domain = parsed.netloc or parsed.hostname or ""
        except Exception:
            domain = ""
        if not domain:
            return

        min_interval = 1.0 / self.domain_rate_limit_per_sec
        try:
            async with self._rate_lock:
                now = time.monotonic()
                last = self._domain_last_action.get(domain, 0.0)
                wait_for = last + min_interval - now
                if wait_for > 0:
                    await asyncio.sleep(wait_for)
                    now = time.monotonic()
                self._domain_last_action[domain] = now
        except Exception:
            log_exception("HumanScheduler.rate_limit")

    async def move_mouse_to(self, page: Page, locator: Locator) -> None:
        if not self.enable_mouse_move:
            return
        try:
            handle = await locator.element_handle()
            if not handle:
                return
            box = await handle.bounding_box()
            if not box:
                return
            target_x = box["x"] + box["width"] / 2
            target_y = box["y"] + box["height"] / 2
            await page.mouse.move(target_x, target_y, steps=5)
        except Exception:
            log_exception("HumanScheduler.move_mouse_to")

HUMAN_SCHEDULER = HumanScheduler(load_or_create_human_config(HUMAN_CONFIG_PATH))

async def close_blocking_popups(page: "Page", timeout_ms: int = 1500) -> None:
    close_selectors = [
        'button[aria-label="Close"]',
        '[aria-label="Close"]',
        '.modal .close',
        '.btn-close',
        '[data-dismiss="modal"]',
        '[data-bs-dismiss="modal"]',
        '.reveal-modal .close-reveal-modal',
        '.lightbox .close',
        '.popup .close',
        'button:has-text("×")',
        'button:has-text("Close")',
        'a:has-text("×")',
    ]
    for sel in close_selectors:
        try:
            loc = page.locator(sel)
            cnt = await loc.count()
            for i in range(cnt):
                el = loc.nth(i)
                if await el.is_visible():
                    try:
                        await el.click(timeout=timeout_ms, force=True)
                        await page.wait_for_timeout(100)
                    except Exception:
                        pass
        except Exception:
            pass

    decline_selectors = [
        'button:has-text("No thanks")',
        'button:has-text("Not now")',
        'a:has-text("No thanks")',
        'a:has-text("Not now")',
    ]
    for sel in decline_selectors:
        try:
            loc = page.locator(sel)
            if await loc.first.is_visible():
                try:
                    await loc.first.click(timeout=timeout_ms, force=True)
                    await page.wait_for_timeout(100)
                except Exception:
                    pass
        except Exception:
            pass

    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(50)
    except Exception:
        pass

    try:
        await page.evaluate("""
        () => {
          const kill = s => document.querySelectorAll(s).forEach(el => el.remove());
          const hide = s => document.querySelectorAll(s).forEach(el => { el.style.display='none'; el.style.visibility='hidden'; });
          kill('.modal-backdrop, .reveal-modal-bg, .lightbox-backdrop, .ui-widget-overlay');
          hide('.modal, [role="dialog"], [aria-modal="true"], .reveal-modal, .lightbox, .popup');
        }
        """)
    except Exception:
        pass

BAD_PROXIES_FILE = DATA_DIR / "bad_proxies.txt"   
LAST_GOOD_PROXIES_FILE = DATA_DIR / "last_good_proxies.json"

def proxy_id(p: Dict) -> str:
    return f"{p.get('server','')}|{p.get('username','')}"

def load_bad_proxies() -> set:
    BAD_PROXIES_FILE.parent.mkdir(exist_ok=True)
    if BAD_PROXIES_FILE.exists():
        return set(x.strip() for x in BAD_PROXIES_FILE.read_text(encoding="utf-8").splitlines() if x.strip())
    return set()

def save_bad_proxy(p: Dict, reason: str = ""):
    pid = proxy_id(p)
    existing = load_bad_proxies()
    if pid in existing:
        return
    with BAD_PROXIES_FILE.open("a", encoding="utf-8") as f:
        f.write(pid + "\n")
    log_line(LOGS_DIR / "proxies_rotation.log", f"[{time.strftime('%H:%M:%S')}] BAD_PROXY saved: {pid} | {reason}")

GOOD_PROXIES_FILE = DATA_DIR / "good_proxies.txt"

def load_good_proxies() -> set:
    """
    Load set of good proxy IDs from data/good_proxies.txt.
    """
    GOOD_PROXIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not GOOD_PROXIES_FILE.exists():
        return set()
    try:
        lines = GOOD_PROXIES_FILE.read_text(encoding="utf-8").splitlines()
        return {x.strip() for x in lines if x.strip()}
    except Exception:
        log_exception("load_good_proxies")
        return set()


def save_good_proxy(p: Dict) -> None:
    """
    Append proxy ID to good_proxies.txt if not already present.
    """
    try:
        pid = proxy_id(p)
        existing = load_good_proxies()
        if pid in existing:
            return
        with GOOD_PROXIES_FILE.open("a", encoding="utf-8") as f:
            f.write(pid + "\n")
        log_line(
            LOGS_DIR / "proxies_rotation.log",
            f"[{time.strftime('%H:%M:%S')}] GOOD_PROXY saved: {pid}",
        )
    except Exception:
        log_exception("save_good_proxy")

def load_last_good_proxy_ids() -> List[str]:
    """
    Load ordered list of last good proxy IDs from data/last_good_proxies.json.
    """
    try:
        if not LAST_GOOD_PROXIES_FILE.exists():
            return []
        data = json.loads(LAST_GOOD_PROXIES_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        out: List[str] = []
        for item in data:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
        return out
    except Exception:
        log_exception("load_last_good_proxy_ids")
        return []


def save_last_good_proxy_ids(proxies: List[Dict]) -> None:
    """
    Save ordered list of proxy IDs that were used in the last run.
    """
    try:
        ids = [proxy_id(p) for p in proxies if p]
        LAST_GOOD_PROXIES_FILE.parent.mkdir(parents=True, exist_ok=True)
        LAST_GOOD_PROXIES_FILE.write_text(
            json.dumps(ids, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        log_exception("save_last_good_proxy_ids")

BAD_PROXY_PAGE_PATTERNS_EXTRA = [
    r"Страница недоступна",
]
BAD_PROXY_PAGE_PATTERNS = CONFIG.bad_proxy_page_patterns + BAD_PROXY_PAGE_PATTERNS_EXTRA

async def detect_bad_proxy_on_page(page: Page) -> Optional[str]:
    try:
        url_now = page.url or ""
        if url_now.startswith("chrome-error://") or url_now.startswith("about:error"):
            return "chrome-error page"
    except Exception:
        pass

    body = ""
    try:
        body = await page.locator("body").inner_text()
    except Exception:
        body = ""

    for pat in BAD_PROXY_PAGE_PATTERNS:
        if re.search(pat, body, flags=re.I):
            return pat
    return None

def load_proxies(csv_path: Path) -> List[Dict]:
    df = pd.read_csv(csv_path)
    proxies: List[Dict] = []
    low = {c.lower(): c for c in df.columns}

    def add_proxy(server_scheme, host, port, user=None, pwd=None):
        server = f"{server_scheme}://{host}:{port}"
        item = {"server": server}
        if user is not None and str(user) != "" and str(user).lower() != "nan":
            item["username"] = str(user)
            item["password"] = str(pwd or "")
        proxies.append(item)

    if "proxy" in low:
        col = low["proxy"]
        for raw in df[col].astype(str):
            s = raw.strip()
            if not s or s.lower() == "nan":
                continue
            if "://" in s:  
                scheme, rest = s.split("://", 1)
                user = pwd = None
                if "@" in rest:
                    auth, hostport = rest.split("@", 1)
                    if ":" in auth:
                        user, pwd = auth.split(":", 1)
                    else:
                        user, pwd = auth, ""
                else:
                    hostport = rest
                if ":" not in hostport:
                    continue
                host, port = hostport.rsplit(":", 1)
                add_proxy(scheme, host, port, user, pwd)
                continue
            parts = s.split(":")
            if len(parts) == 4:              
                host, port, user, pwd = parts
                add_proxy("http", host, port, user, pwd)
            elif len(parts) == 2:            
                host, port = parts
                add_proxy("http", host, port, None, None)
            else:
                continue
        return proxies

    if {"host", "port"}.issubset(low.keys()):
        host_c = low["host"]; port_c = low["port"]
        user_c = low.get("user") or low.get("username") or low.get("login")
        pwd_c  = low.get("pass") or low.get("password") or low.get("pwd")
        for _, r in df.iterrows():
            host, port = str(r[host_c]).strip(), str(r[port_c]).strip()
            user = (str(r[user_c]).strip() if user_c else None)
            pwd  = (str(r[pwd_c]).strip() if pwd_c else None)
            add_proxy("http", host, port, user, pwd)
        return proxies

    first = df.columns[0]
    for raw in df[first].astype(str):
        s = raw.strip()
        if not s or s.lower() == "nan":
            continue
        if "://" in s:
            scheme, rest = s.split("://", 1)
            user = pwd = None
            if "@" in rest:
                auth, hostport = rest.split("@", 1)
                if ":" in auth: user, pwd = auth.split(":", 1)
                else: user, pwd = auth, ""
            else:
                hostport = rest
            if ":" not in hostport:
                continue
            host, port = hostport.rsplit(":", 1)
            add_proxy(scheme, host, port, user, pwd)
        else:
            parts = s.split(":")
            if len(parts) == 4:
                host, port, user, pwd = parts
                add_proxy("http", host, port, user, pwd)
            elif len(parts) == 2:
                host, port = parts
                add_proxy("http", host, port, None, None)
    return proxies


def pick_user_agent() -> str:
    return random.choice(USER_AGENTS)


def context_args_for(proxy: Optional[Dict]) -> Dict:
    geo = pick_geo_profile()
    viewport = pick_viewport()
    device_scale = round(random.uniform(1.0, 1.25), 2)

    args: Dict[str, object] = {
        "viewport": viewport,
        "device_scale_factor": device_scale,
        "user_agent": pick_user_agent(),
        "java_script_enabled": True,
        "locale": "en-US",
        "timezone_id": str(geo["timezone_id"]),
        "geolocation": {
            "latitude": float(geo["lat"]),
            "longitude": float(geo["lon"]),
            "accuracy": float(random.uniform(10.0, 100.0)),
        },
        "permissions": ["geolocation"],
    }
    if proxy:
        args["proxy"] = proxy
    return args


async def clear_cookies_once(ctx: BrowserContext) -> None:
    try:
        await ctx.clear_cookies()
        await ctx.add_cookies([])
    except Exception:
        log_exception("clear_cookies_once")
    finally:
        pass


async def detect_per_order_limit(page: Page) -> int:
    try:
        body = await page.locator("body").inner_text()
    except Exception:
        body = ""
    m = re.search(r"Limit\s+(\d+)\s+tickets?\s+per\s+order", body, flags=re.I)
    if m:
        return int(m.group(1))

    selects = page.locator("select")
    count = await selects.count()
    max_opt = None
    for i in range(count):
        options = await selects.nth(i).locator("option").all_inner_texts()
        nums = []
        for o in options:
            o = o.strip()
            if o.isdigit():
                nums.append(int(o))
        if nums:
            loc_max = max(nums)
            max_opt = loc_max if max_opt is None or loc_max > max_opt else max_opt
    return max_opt or 8

async def detect_per_order_limit_precise(page: Page, ticket_index: Optional[int]) -> int:
    try:
        sel = await find_ticket_select(page, preferred_index=ticket_index)
        if sel:
            labels = await sel.locator("option").all_inner_texts()
            nums = [int(x.strip()) for x in labels if x.strip().isdigit()]
            if nums:
                return max(nums)
    except Exception:
        pass
    return await detect_per_order_limit(page)

async def accept_cookies_if_present(page: Page):
    try:
        btn = page.get_by_role("button", name=re.compile(r"OK|Accept|I Agree|Соглашаюсь", re.I)).first
        if await btn.count() > 0:
            await btn.click(timeout=3000)
            await page.wait_for_timeout(350)
    except Exception:
        pass

async def close_idle_modal_if_present(page: Page) -> bool:
    try:
        header = page.locator(
            "xpath=//*[contains(translate(normalize-space(.),"
            " 'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),"
            " 'NOT READY TO BUY TICKETS')]"
        ).first
        email_box = page.locator("input[placeholder*='Enter Email Address']").first
        send_ru  = page.get_by_role("button", name=re.compile(r"Отослать", re.I)).first

        if await header.count() > 0 or await email_box.count() > 0 or await send_ru.count() > 0:
            candidates = [
                "button[aria-label='Close']",
                ".modal-header .close",
                ".ui-dialog-titlebar-close",
                "button:has-text('×')",
                "a:has-text('×')",
                "[data-dismiss='modal']",
                "button:has-text('Close')",
                "button:has-text('Cancel')",
                ".modal-backdrop",             
                ".ui-widget-overlay",          
            ]
            for sel in candidates:
                btn = page.locator(sel).first
                if await btn.count() > 0 and await btn.is_visible():
                    try:
                        await btn.click(timeout=1500, force=True)
                        await page.wait_for_timeout(250)
                        return True
                    except Exception:
                        pass
            try:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(200)
                return True
            except Exception:
                pass
            try:
                await page.mouse.click(10, 10)
                await page.wait_for_timeout(200)
                return True
            except Exception:
                pass
    except Exception:
        pass
    try:
        await page.evaluate("""
        () => {
          const kill = sel => document.querySelectorAll(sel).forEach(el => el.remove());
          const hide = sel => document.querySelectorAll(sel).forEach(el => {
            el.style.display = 'none';
            el.style.visibility = 'hidden';
          });
          kill('.modal-backdrop, .ui-widget-overlay, .reveal-modal-bg, .lightboxOverlay, .lean-overlay');
          hide('.modal, [role="dialog"], [aria-modal="true"], .reveal-modal, .lightbox, .popup');
        }
        """)
    except Exception:
        pass
    return False

async def ensure_click_target_clear(page: Page, target_locator) -> None:
    try:
        handle = await target_locator.element_handle()
        if not handle:
            return
        for _ in range(5):
            covered = await page.evaluate(
                """
                (btn) => {
                  const r = btn.getBoundingClientRect();
                  const cx = (r.left + r.right) / 2;
                  const cy = (r.top + r.bottom) / 2;
                  const top = document.elementFromPoint(cx, cy);
                  if (!top) return 'clear';
                  let el = top;
                  // кнопка сама себе не должна мешать
                  while (el) { if (el === btn) return 'clear'; el = el.parentElement; }
                  // прячем перекрывающий элемент
                  top.style.setProperty('display', 'none', 'important');
                  top.style.setProperty('visibility', 'hidden', 'important');
                  return 'hid';
                }
                """,
                handle,
            )
            if covered == "clear":
                break
            await page.wait_for_timeout(60)
    except Exception:
        pass

async def humanized_click_add_button(page: Page, add_btn: Locator) -> None:
    try:
        await HUMAN_SCHEDULER.move_mouse_to(page, add_btn)
    except Exception:
        log_exception("humanized_click_add_button.move_mouse")

    await ensure_click_target_clear(page, add_btn)
    try:
        await add_btn.scroll_into_view_if_needed()
    except Exception:
        pass

    try:
        await HUMAN_SCHEDULER.rate_limit(page.url)
    except Exception:
        log_exception("humanized_click_add_button.rate_limit")

    await HUMAN_SCHEDULER.sleep_pre_click_add_to_cart()

    await add_btn.click(timeout=CLICK_TIMEOUT)

    await HUMAN_SCHEDULER.sleep_post_click_wait()

async def handle_verification_code_expired_and_back(page: Page) -> bool:
    try:
        body = await page.locator("body").inner_text()
    except Exception:
        body = ""

    if re.search(r"verification code expires|SYS-BS-003", body, flags=re.I):
        for sel in [
            "button:has-text('Назад')",
            "button:has-text('Back')",
            "input[type='submit'][value='Back']",
            "button:has-text('OK')",
            "input[type='submit'][value='OK']",
        ]:
            el = page.locator(sel).first
            if await el.count() > 0:
                try:
                    await el.click(timeout=3000)
                    await page.wait_for_load_state("domcontentloaded")
                    return True
                except Exception:
                    pass
        try:
            await page.go_back(wait_until="domcontentloaded", timeout=10000)
            return True
        except Exception:
            pass
    return False

async def clear_cart(page: Page):
    try:
        for sel in ("text=CLEAR SHOPPING CART", "text=Clear shopping cart",
                    "text=Удалить всё", "text=Remove All", "text=Delete All",
                    "css=input[type=submit][value*='CLEAR SHOPPING CART']"):
            el = page.locator(sel).first
            if await el.count() > 0:
                await el.click(timeout=3000)
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(500)
                return
        removes = page.locator("text=REMOVE, css=input[type=submit][value*='REMOVE']")
        rc = await removes.count()
        for i in range(rc):
            await removes.nth(i).click(timeout=3000)
            await page.wait_for_timeout(120)
    except Exception:
        pass


async def _nearest_text(sel: Page) -> str:
    try:
        parent = sel.locator("xpath=ancestor::*[self::tr or self::li or self::div][1]")
        if await parent.count() > 0:
            txt = (await parent.inner_text()).strip()
            if txt:
                return txt
    except Exception:
        log_debug("_nearest_text.parent failed")
    try:
        return (await sel.inner_text()).strip()
    except Exception:
        log_debug("_nearest_text.inner_text failed")
        return ""

def _is_global_quantity_text(text: str) -> bool:
    t = (text or "").lower()
    return (
        "количество билетов" in t or
        "quantity" in t or
        bool(re.search(r"limit\s+\d+\s+tickets?\s+per\s+order", t, re.I))
    )

async def find_ticket_select(page: Page,
                             preferred_index: Optional[int] = None):
    selects = page.locator("select")

    candidates = []
    count = await selects.count()
    for i in range(count):
        sel = selects.nth(i)
        if not await sel.is_visible():
            continue
        txt = await _nearest_text(sel)
        if not _is_global_quantity_text(txt):
            candidates.append(sel)

    if preferred_index is not None and len(candidates) > 0:
        n = len(candidates)
        idx = preferred_index - 1 if preferred_index > 0 else n + preferred_index
        if 0 <= idx < n:
            return candidates[idx]

    if candidates:
        return candidates[0]

    count = await selects.count()
    for i in range(count):
        sel = selects.nth(i)
        if await sel.is_visible():
            return sel

    return None


async def wait_cart_success(page: Page) -> bool:
    try:
        await page.wait_for_url(re.compile(r"/(cart|basket)(/|$)", re.I), timeout=7000)
        return True
    except Exception:
        pass

    try:
        await page.wait_for_load_state("networkidle", timeout=7000)
    except Exception:
        pass

    try:
        checkout_btn = page.get_by_role(
            "button",
            name=re.compile(r"(PROCEED\s+TO\s+)?CHECKOUT|PROCESS\s+ORDER", re.I),
        ).first
        if await checkout_btn.count() > 0 and await checkout_btn.is_visible():
            return True
    except Exception:
        pass

    try:
        remove_any = page.locator(
            ":is(a,button):has-text('REMOVE'), "
            ":is(a,button):has-text('Remove All'), "
            ":is(a,button):has-text('CLEAR SHOPPING CART')"
        ).first
        if await remove_any.count() > 0 and await remove_any.is_visible():
            return True

        totals = page.locator(
            ":text('Subtotal'), :text('Order Fee'), :text('Order Total'), :text('Service Fee')"
        )
        if await totals.count() > 0:
            return True

        heading = page.get_by_role(
            "heading",
            name=re.compile(r"(Your\s+)?Shopping\s+Cart", re.I),
        ).first
        if await heading.count() > 0 and await heading.is_visible():
            return True

        cont = page.get_by_role(
            "link",
            name=re.compile(r"Continue\s+Shopping", re.I),
        ).first
        if await cont.count() > 0 and await cont.is_visible():
            return True

        cont_btn = page.get_by_role(
            "button",
            name=re.compile(r"Continue\s+Shopping", re.I),
        ).first
        if await cont_btn.count() > 0 and await cont_btn.is_visible():
            return True
    except Exception:
        pass

    try:
        body = await page.locator("body").inner_text()
        if re.search(r"(Your\s+)?Shopping\s+Cart", body, flags=re.I):
            return True
        if re.search(
            r"(Subtotal|Order\s*Fee|Order\s*Total|REMOVE|Remove\s+All|CLEAR\s*SHOPPING\s*CART)",
            body,
            flags=re.I,
        ):
            return True
        if re.search(r"Continue\s+Shopping", body, flags=re.I):
            return True
    except Exception:
        pass

    return False


async def _robust_select_quantity(sel, qty: int) -> bool:
    try:
        await sel.scroll_into_view_if_needed()
    except Exception:
        pass

    try:
        await sel.select_option(str(qty))
        val = await sel.input_value()
        if val.strip() == str(qty):
            return True
    except Exception:
        pass

    try:
        await sel.select_option(label=str(qty))
        val = await sel.input_value()
        if val.strip() == str(qty):
            return True
    except Exception:
        pass

    try:
        options = await sel.locator("option").all()
        labels = [await o.inner_text() for o in options]
        nums = [int(x.strip()) for x in labels if x.strip().isdigit()]
        if nums:
            alt = max([n for n in nums if n >= qty] or [max(nums)])
            try:
                await sel.select_option(str(alt))
            except Exception:
                idx = None
                for j, o in enumerate(options):
                    t = (await o.inner_text()).strip()
                    if t.isdigit() and int(t) == alt:
                        idx = j
                        break
                if idx is not None:
                    await sel.evaluate("(el, i) => { el.selectedIndex = i; el.dispatchEvent(new Event('change', {bubbles:true})); }", idx)
            val = (await sel.input_value()).strip()
            if val and val.isdigit() and int(val) == alt:
                return True
    except Exception:
        pass

    try:
        await sel.click()
        await sel.press("Home")
        for _ in range(qty):
            await sel.press("ArrowDown")
        await sel.press("Enter")
        await asyncio.sleep(0.05)
        val = await sel.input_value()
        if val.strip().isdigit():
            return True
    except Exception:
        pass

    return False


async def select_quantity_and_add(page: Page,
                                  qty: int,
                                  ticket_index: Optional[int] = None) -> Tuple[bool, str]:
    server_busy_retried = False
    await accept_cookies_if_present(page)
    await close_idle_modal_if_present(page)
    try:
        await page.wait_for_selector("select", timeout=12000)
    except Exception:
        return False, "Не найден селект количества"

    sel = await find_ticket_select(page, preferred_index=ticket_index)
    if not sel:
        return False, "Не найден селект количества"

    ok_qty = await _robust_select_quantity(sel, qty)
    if not ok_qty:
        return False, "Не удалось выбрать количество в селекте"

    add_btn = page.get_by_role(
        "button",
        name=re.compile(r"ADD\s*TICKETS|ДОБАВИТЬ\s*БИЛЕТЫ", re.I),
    ).first
    if await add_btn.count() == 0:
        add_btn = page.locator(
            "button:has-text('ADD TICKETS'), "
            "input[type='submit'][value*='ADD TICKETS']"
        ).first
    if await add_btn.count() == 0:
        add_btn = page.locator(
            "xpath=//a[contains(translate(normalize-space(.), "
            "'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'),"
            "'ADD TICKETS')]"
        ).first
    if await add_btn.count() == 0:
        return False, "Кнопка ADD TICKETS не найдена"

    await close_idle_modal_if_present(page)
    try:
        await humanized_click_add_button(page, add_btn)
    except Exception:
        log_exception("select_quantity_and_add.humanized_click.initial")
        await close_idle_modal_if_present(page)
        try:
            await humanized_click_add_button(page, add_btn)
        except Exception:
            log_exception("select_quantity_and_add.humanized_click.retry")
            return False, "Ошибка клика по ADD TICKETS"

    ok = await wait_cart_success(page)
    if ok:
        await close_idle_modal_if_present(page)
        return True, "OK"

    try:
        if await add_btn.count() > 0:
            await add_btn.scroll_into_view_if_needed()
            await add_btn.click(timeout=5000, force=True)
            if await wait_cart_success(page):
                await close_idle_modal_if_present(page)
                return True, "OK (retry click)"
    except Exception:
        log_exception("select_quantity_and_add.retry_click")

    try:
        alert = page.locator("div.alert-danger, .alert-danger, .alert-error").first
        if await alert.count() > 0:
            txt = await alert.inner_text()
            if is_inventory_message(txt):
                return False, "not enough tickets of that type available"
    except Exception:
        pass
    try:
        body_now = await page.locator("body").inner_text()
        if is_select_qty_prompt(body_now):
            return False, "Количество не выбрано (UI-ошибка)"
        if is_inventory_message(body_now):
            return False, "not enough tickets of that type available"
    except Exception:
        pass

    did_back = await handle_verification_code_expired_and_back(page)
    if did_back:
        try:
            await page.wait_for_selector("select, input[type='submit'][value*='ADD TICKETS']", timeout=8000)
        except Exception:
            try:
                await page.reload(wait_until="domcontentloaded")
                await page.wait_for_selector("select, input[type='submit'][value*='ADD TICKETS']", timeout=8000)
            except Exception:
                return False, "Back->не дождались формы шоу"

        await accept_cookies_if_present(page)
        await close_idle_modal_if_present(page)

        sel = await find_ticket_select(page, preferred_index=ticket_index)
        if not sel:
            return False, "Не найден селект количества"
        if not await _robust_select_quantity(sel, qty):
            return False, "Back->не удалось выбрать количество"

        add_btn2 = page.get_by_role(
            "button",
            name=re.compile(r"ADD\s*TICKETS|ДОБАВИТЬ\s*БИЛЕТЫ", re.I),
        ).first
        if await add_btn2.count() == 0:
            add_btn2 = page.locator(
                "button:has-text('ADD TICKETS'), "
                "input[type='submit'][value*='ADD TICKETS']"
            ).first
        if await add_btn2.count() > 0:
            try:
                await humanized_click_add_button(page, add_btn2)
            except Exception:
                log_exception("select_quantity_and_add.back_retry_click")
                return False, "Back->повтор ADD неуспешен (ошибка клика)"

            if await wait_cart_success(page):
                await close_idle_modal_if_present(page)
                return True, "OK (после Back)"

        return False, "Back->повтор ADD неуспешен"


    try:
        alert = page.locator("div.alert-danger, .alert-danger, .alert-error").first
        if await alert.count() > 0:
            txt = await alert.inner_text()
            if is_inventory_message(txt):
                return False, "Сайт попросил уменьшить количество (alert)"
    except Exception:
        pass

    try:
        body = await page.locator("body").inner_text()
        if is_inventory_message(body):
            return False, "Сайт попросил уменьшить количество или билетов нет"
    except Exception:
        pass

    try:
        body = await page.locator("body").inner_text()
        if (not server_busy_retried) and re.search(r"сервер\s+перегружен|server\s+busy|try\s+again\s+in\s+a\s+few\s+minutes", body, re.I):
            server_busy_retried = True
            try:
                await page.go_back(wait_until="domcontentloaded", timeout=10000)
            except Exception:
                await page.reload()
            await accept_cookies_if_present(page)
            await close_idle_modal_if_present(page)
            sel = await find_ticket_select(page, preferred_index=ticket_index)
            if sel and await _robust_select_quantity(sel, qty):
                add_btn = page.get_by_role(
                    "button",
                    name=re.compile(r"ADD\s*TICKETS|ДОБАВИТЬ\s*БИЛЕТЫ", re.I),
                ).first
                if await add_btn.count() == 0:
                    add_btn = page.locator(
                        "button:has-text('ADD TICKETS'), "
                        "input[type='submit'][value*='ADD TICKETS']"
                    ).first
                if await add_btn.count() > 0:
                    try:
                        await humanized_click_add_button(page, add_btn)
                    except Exception:
                        log_exception("select_quantity_and_add.server_busy_retry_click")
                    else:
                        if await wait_cart_success(page):
                            return True, "OK (после retry)"
    except Exception:
        pass

    return False, "Не похоже, что билеты попали в корзину"

async def open_show_on_page(page: Page, url: str, tab_idx: int) -> bool:
    try:
        if page.is_closed():
            return False
        try:
            await HUMAN_SCHEDULER.rate_limit(url)
        except Exception:
            log_exception(f"open_show_on_page.rate_limit.initial[TAB#{tab_idx}]")

        await HUMAN_SCHEDULER.sleep_between_navigations()

        await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        await accept_cookies_if_present(page)
        await close_idle_modal_if_present(page)
        await close_blocking_popups(page)
        try:
            if await is_soldout_page(page) or await is_ended_reliable(page):
                return True
        except Exception:
            pass
        try:
            await page.wait_for_selector(
                "select, input[type='submit'][value*='ADD TICKETS']",
                timeout=15000,
            )
        except Exception:
            pass
        return True
    except Exception as exc:
        log_exception(f"open_show_on_page.goto[TAB#{tab_idx}]")
        if _is_target_closed_exception(exc):
            return False

    try:
        if page.is_closed():
            return False
        try:
            await HUMAN_SCHEDULER.rate_limit(url)
        except Exception:
            log_exception(f"open_show_on_page.rate_limit.retry[TAB#{tab_idx}]")
        try:
            await page.wait_for_timeout(500)
        except Exception:
            pass
        await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        await accept_cookies_if_present(page)
        await close_idle_modal_if_present(page)
        await close_blocking_popups(page)
        return True
    except Exception as exc:
        log_exception(f"open_show_on_page.retry[TAB#{tab_idx}]")
        if _is_target_closed_exception(exc):
            return False
        return False


async def focus_tab(page: Page, tab_idx: int) -> None:
    try:
        if page.is_closed():
            return
        await page.bring_to_front()
        try:
            await page.evaluate("window.focus()")
        except Exception:
            pass
    except Exception as exc:
        if _is_target_closed_exception(exc):
            return
        log_exception(f"focus_tab[TAB#{tab_idx}]")


async def wait_for_manual_cart(
    page: Page,
    tab_idx: int,
    show_url: str,
    limit: int,
    ticket_index: Optional[int],
    timeout_s: int = 60,
    auto_solve_captcha: Optional[Callable[[], Awaitable[Tuple[bool, str]]]] = None,
) -> Tuple[bool, str, int, bool]:
    """
    Wait for user to solve CAPTCHA and reach cart.
    Returns (ok, msg, attempts_used, inventory_abort).
    """
    deadline = time.monotonic() + max(1, timeout_s)
    last_attempt = 0.0
    last_auto_attempt = 0.0
    attempts_used = 0
    inventory_abort = False

    while time.monotonic() < deadline:
        try:
            if page.is_closed():
                return False, "Page closed", attempts_used, inventory_abort
        except Exception:
            pass

        await focus_tab(page, tab_idx)

        if await is_cart_page(page):
            return True, "OK (manual cart)", attempts_used, inventory_abort

        try:
            if await is_begin_captcha(page):
                await click_begin_captcha(page)
                await asyncio.sleep(1)
                continue
        except Exception:
            log_exception(f"wait_for_manual_cart.begin_captcha[TAB#{tab_idx}]")

        try:
            if await is_captcha(page):
                now_auto = time.monotonic()
                if auto_solve_captcha and (now_auto - last_auto_attempt) >= 4:
                    last_auto_attempt = now_auto
                    try:
                        solved, solve_msg = await auto_solve_captcha()
                    except Exception:
                        solved, solve_msg = False, "auto solve callback exception"
                        log_exception(f"wait_for_manual_cart.auto_solve[TAB#{tab_idx}]")
                    if solved:
                        try:
                            ok_try, msg_try = await select_quantity_and_add(
                                page, limit, ticket_index
                            )
                        except Exception as exc:
                            ok_try, msg_try = False, f"Exception: {exc}"
                            log_exception(f"wait_for_manual_cart.auto_add[TAB#{tab_idx}]")
                        attempts_used += 1
                        if ok_try:
                            return True, f"{msg_try} ({solve_msg})", attempts_used, inventory_abort
                        if is_inventory_message(str(msg_try)):
                            inventory_abort = True
                            return False, msg_try, attempts_used, inventory_abort
                await asyncio.sleep(1)
                continue
        except Exception:
            log_exception(f"wait_for_manual_cart.is_captcha[TAB#{tab_idx}]")

        now = time.monotonic()
        if now - last_attempt >= 5:
            try:
                ok_try, msg_try = await select_quantity_and_add(
                    page, limit, ticket_index
                )
            except Exception as exc:
                ok_try, msg_try = False, f"Exception: {exc}"
                log_exception(f"wait_for_manual_cart.add[TAB#{tab_idx}]")

            attempts_used += 1
            last_attempt = now

            if ok_try:
                return True, msg_try, attempts_used, inventory_abort
            if is_inventory_message(str(msg_try)):
                inventory_abort = True
                return False, msg_try, attempts_used, inventory_abort

        await asyncio.sleep(1)

    return False, "CAPTCHA timeout", attempts_used, inventory_abort

async def _internal_check(shows_csv_path: Path = SHOWS_CSV):
    if not PROXIES_CSV.exists():
        raise FileNotFoundError(f"Не найден файл прокси: {PROXIES_CSV}")
    if not shows_csv_path.exists():
        if shows_csv_path == SHOWS_CSV:
            SHOWS_CSV.write_text(
                "url,target_total,max_per_order,ticket_index\n"
                "https://www.etix.com/ticket/p/92758929/level-up-wtba-raleigh-lincoln-theatre,96,,3\n",
                encoding="utf-8"
            )
            raise FileNotFoundError(f"Создан шаблон {SHOWS_CSV}. Заполните его и запустите снова.")
        raise FileNotFoundError(f"Не найден файл shows.csv: {shows_csv_path}")

TRACKING_KEYS = CONFIG.tracking_keys

def _normalize_url(url: str) -> str:
    try:
        u = urlparse(url.strip())
        scheme = (u.scheme or "http").lower()
        netloc = u.netloc.lower()
        path = u.path or "/"
        qs = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=False)
                if k and v and k.lower() not in TRACKING_KEYS]
        query = urlencode(qs, doseq=True)
        return urlunparse((scheme, netloc, path, "", query, ""))
    except Exception:
        return url.strip()

def make_show_id(name: str, url: str) -> str:
    base = f"{(name or '').strip().lower()}|{_normalize_url(url)}".encode("utf-8", "ignore")
    return hashlib.sha1(base).hexdigest()

def _json_atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)

def _fingerprint_show_ids(ids: List[str]) -> str:
    blob = "\n".join(sorted(ids)).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()

def _read_checkpoint(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

def _find_last_active_checkpoint(run_root: Path = RUNS_ROOT) -> Optional[Path]:
    if not run_root.exists():
        return None
    candidates = []
    for p in run_root.iterdir():
        cp = p / "checkpoint.json"
        if cp.exists():
            try:
                st = cp.stat()
                candidates.append((st.st_mtime, cp))
            except Exception:
                pass
    candidates.sort(key=lambda x: x[0], reverse=True)
    for _, cp in candidates:
        data = _read_checkpoint(cp)
        if not data:
            continue
        done = int(data.get("counters", {}).get("done_count", 0))
        total = int(data.get("shows_total", 0))
        if total and done < total:
            return cp
    return None

def checkpoint_probe(run_root: Path = RUNS_ROOT) -> Optional[dict]:
    cp = _find_last_active_checkpoint(run_root)
    if not cp:
        return None
    data = _read_checkpoint(cp)
    if not data:
        return None
    return {
        "run_id": data.get("run_id"),
        "shows_total": data.get("shows_total", 0),
        "done_count": data.get("counters", {}).get("done_count", 0),
        "shows_fingerprint": data.get("shows_fingerprint", ""),
        "path": str(cp),
    }

def drop_last_active_run(run_root: Path = RUNS_ROOT) -> bool:
    cp = _find_last_active_checkpoint(run_root)
    if not cp:
        return False
    run_dir = cp.parent
    try:
        for root, dirs, files in os.walk(run_dir, topdown=False):
            for f in files:
                try:
                    os.remove(Path(root) / f)
                except Exception as exc:
                    log_debug(f"drop_last_active_run.remove_file failed: {exc}")
            for d in dirs:
                try:
                    os.rmdir(Path(root) / d)
                except Exception as exc:
                    log_debug(f"drop_last_active_run.remove_dir failed: {exc}")
        os.rmdir(run_dir)
        return True
    except Exception:
        return False

def current_csv_fingerprint(shows_csv_path: Optional[Path] = None) -> tuple[str, int]:
    try:
        path = shows_csv_path or SHOWS_CSV
        df = pd.read_csv(path).fillna("")
        df.rename(columns={c: c.lower() for c in df.columns}, inplace=True)
        ids: List[str] = []
        if "url" not in df.columns:
            return "", 0
        for _, r in df.iterrows():
            url = str(r["url"]).strip()
            if not re.match(r"^https?://", url, flags=re.I):
                continue
            name = str(r.get("name", "")).strip() or url
            ids.append(make_show_id(name, url))
        return _fingerprint_show_ids(ids), len(ids)
    except Exception:
        log_exception("current_csv_fingerprint")
        return "", 0

class RunContext:
    def __init__(self, run_root: Path = RUNS_ROOT) -> None:
        self.run_root = Path(run_root)
        self.lock = asyncio.Lock()
        self.run_id: Optional[str] = None
        self.dir: Optional[Path] = None
        self.cp_path: Optional[Path] = None
        self.state: dict = {}

    def init_new_run(self, shows: List[dict], app_params: dict) -> str:
        self.run_id = time.strftime("%Y-%m-%dT%H-%M-%S")
        self.dir = self.run_root / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        ids = [s["show_id"] for s in shows]
        fp = _fingerprint_show_ids(ids)
        self.cp_path = self.dir / "checkpoint.json"
        self.state = {
            "run_id": self.run_id,
            "version": APP_VERSION,
            "shows_total": len(ids),
            "shows_fingerprint": fp,
            "done": [],
            "inflight": [],
            "pending": ids.copy(),
            "counters": {"done_count": 0},
        }
        _json_atomic_write(self.dir / "params.json", {
            "run_id": self.run_id,
            "version": APP_VERSION,
            "shows_fingerprint": fp,
            "params": app_params,
        })
        _json_atomic_write(self.cp_path, self.state)
        return self.run_id

    def try_load_active_run(self) -> Optional[dict]:
        cp = _find_last_active_checkpoint(self.run_root)
        if not cp:
            return None
        data = _read_checkpoint(cp)
        if not data:
            return None
        self.run_id = data.get("run_id")
        self.dir = cp.parent
        self.cp_path = cp
        self.state = data
        return data

    def adopt_existing_run(self, shows: List[dict]) -> List[str]:
        ids_now = [s["show_id"] for s in shows]
        fp_now = _fingerprint_show_ids(ids_now)
        if fp_now != self.state.get("shows_fingerprint"):
            raise RuntimeError("Список shows.csv изменился; возобновление отклонено.")
        valid = set(ids_now)
        inflight = [sid for sid in self.state.get("inflight", []) if sid in valid]
        pending = [sid for sid in self.state.get("pending", []) if sid in valid]
        return inflight + pending

    async def mark_inflight(self, show_id: str) -> None:
        async with self.lock:
            if show_id in self.state.get("pending", []):
                self.state["pending"].remove(show_id)
            if show_id not in self.state.get("inflight", []):
                self.state.setdefault("inflight", []).append(show_id)
            _json_atomic_write(self.cp_path, self.state)

    async def commit_done(self, show_id: str, row: dict) -> None:
        async with self.lock:
            infl = self.state.get("inflight", [])
            if show_id in infl:
                infl.remove(show_id)
            self.state.setdefault("done", []).append({"show_id": show_id, "row": row})
            self.state.setdefault("counters", {})["done_count"] = len(self.state["done"])
            _json_atomic_write(self.cp_path, self.state)

    async def complete_run(self) -> None:
        try:
            if self.cp_path and self.cp_path.exists():
                os.remove(self.cp_path)
        except Exception:
            pass


async def check_shows(
    on_show_done: Optional[Callable[[dict, int, int], Awaitable[None]]] = None,
    resume_mode: str = "auto",          
    run_root: str | os.PathLike = "runs",
    manual_captcha_first_show: bool = False,
    use_same_proxies: bool = False,
    shows_path: Optional[str | os.PathLike] = None,
    ) -> list:
    ensure_dirs()
    shows_csv_path = Path(shows_path) if shows_path else SHOWS_CSV
    await _internal_check(shows_csv_path)

    captcha_solver = TwoCaptchaFunCaptchaSolver(
        config=CaptchaSolverConfig.from_env(),
        logger=LOGGER,
    )
    captcha_auto_disabled_reason = ""

    def _is_2captcha_fatal_error(raw_text: str) -> bool:
        text = str(raw_text or "").lower()
        fatal_markers = (
            "error_zero_balance",
            "error_wrong_user_key",
            "error_key_does_not_exist",
            "error_ip_not_allowed",
            "missing api key",
            "missing api-key",
            "missing api key",
        )
        return any(marker in text for marker in fatal_markers)

    if captcha_solver.is_active:
        log_line(LOGS_DIR / "network_health.log", "[INFO] 2Captcha auto-solver is enabled")
    else:
        log_line(LOGS_DIR / "network_health.log", "[INFO] 2Captcha auto-solver is disabled")

    global HEADLESS
    headless_original = HEADLESS
    run_headless = HEADLESS
    if manual_captcha_first_show:
        if HEADLESS:
            print("WARNING: Manual captcha mode forces HEADLESS=False for this run.")
        HEADLESS = False
        run_headless = False

    all_proxies = load_proxies(PROXIES_CSV)
    if len(all_proxies) == 0:
        raise RuntimeError("Список прокси пуст")

    bad_ids = load_bad_proxies()
    all_proxies = [p for p in all_proxies if proxy_id(p) not in bad_ids]
    if not all_proxies:
        raise RuntimeError("Все прокси в чёрном списке. Очистите data/bad_proxies.txt или добавьте новые прокси.")

    same_proxy_ids_raw: List[str] = []
    if use_same_proxies:
        same_proxy_ids_raw = load_last_good_proxy_ids()
        if not same_proxy_ids_raw:
            use_same_proxies = False
            log_line(LOGS_DIR / "proxies_rotation.log", "[INFO] SAME_PROXIES requested but no saved list found.")

    same_proxy_ids: List[str] = []
    if use_same_proxies and same_proxy_ids_raw:
        seen_ids: set[str] = set()
        for pid in same_proxy_ids_raw:
            if pid and pid not in seen_ids:
                same_proxy_ids.append(pid)
                seen_ids.add(pid)

    shows_df = pd.read_csv(shows_csv_path).fillna("")
    if shows_df.empty:
        raise RuntimeError("Файл shows.csv пуст")
    rename_map = {c: c.lower() for c in shows_df.columns}
    shows_df.rename(columns=rename_map, inplace=True)
    if "url" not in shows_df.columns:
        raise RuntimeError("В файле shows.csv обязателен столбец 'url'")

    shows_list: List[dict] = []
    for _, r in shows_df.iterrows():
        url = str(r["url"]).strip()
        if not re.match(r"^https?://", url, flags=re.I):
            continue
        item = {
            "url": url,
            "name": (str(r.get("name", "")).strip() or url),
            "target_total": str(r.get("target_total", "")).strip(),
            "max_per_order": str(r.get("max_per_order", "")).strip(),
            "ticket_index": r.get("ticket_index", ""),
        }
        item["show_id"] = make_show_id(item["name"], item["url"])
        shows_list.append(item)
    total_valid = len(shows_list)
    id2show = {s["show_id"]: s for s in shows_list}

    run_ctx = RunContext(Path(run_root))
    summary_rows: List[dict] = []

    if resume_mode == "resume":
        loaded = run_ctx.try_load_active_run()
        if not loaded:
            raise RuntimeError("Нет активного незавершённого прогона для возобновления.")
        for d in loaded.get("done", []):
            row_done = d.get("row")
            if row_done:
                summary_rows.append(row_done)
        queue_ids = run_ctx.adopt_existing_run(shows_list)
        todo = [id2show[sid] for sid in queue_ids]
        total_valid = int(loaded.get("shows_total", total_valid) or total_valid)
    else:
        run_ctx.init_new_run(
            shows_list,
            app_params={
                "HEADLESS": HEADLESS,
                "SLOWMO_MS": SLOWMO_MS,
                "TABS_COUNT": TABS_COUNT,
                "RANDOMIZE_PROXIES": RANDOMIZE_PROXIES,
                "MANUAL_CAPTCHA_FIRST_SHOW": manual_captcha_first_show,
                "USE_SAME_PROXIES": use_same_proxies,
                "SHOWS_CSV": str(shows_csv_path),
            },
        )
        todo = shows_list

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=run_headless, slow_mo=SLOWMO_MS)

        proxy_cursor = 0
        in_use: set[str] = set()
        captcha_blocked_ids: set[str] = set()

        id_map = {proxy_id(p): p for p in all_proxies}
        same_pool: List[Dict] = []
        if use_same_proxies and same_proxy_ids:
            for pid in same_proxy_ids:
                p = id_map.get(pid)
                if p:
                    same_pool.append(p)
        same_proxy_ids_set = {proxy_id(p) for p in same_pool}

        good_ids = load_good_proxies()
        good_pool: List[Dict] = [p for p in all_proxies if proxy_id(p) in good_ids]
        def next_good_proxy(avoid_captcha: bool = False) -> Optional[Dict]:
            nonlocal proxy_cursor, all_proxies, in_use, bad_ids, captcha_blocked_ids
            try:
                if not all_proxies:
                    return None

                if RANDOMIZE_PROXIES:
                    candidates: List[Dict] = []
                    for p in all_proxies:
                        pid = proxy_id(p)
                        if pid in bad_ids or pid in in_use:
                            continue
                        if avoid_captcha and pid in captcha_blocked_ids:
                            continue
                        candidates.append(p)

                    if candidates:
                        return random.choice(candidates)

                    if avoid_captcha:
                        fallback = [
                            p for p in all_proxies
                            if proxy_id(p) not in bad_ids and proxy_id(p) not in in_use
                        ]
                        if fallback:
                            return random.choice(fallback)

                    fallback_any = [
                        p for p in all_proxies
                        if proxy_id(p) not in bad_ids and proxy_id(p) not in in_use
                    ]
                    return random.choice(fallback_any) if fallback_any else None

                total = len(all_proxies)
                tried = 0
                while tried < total:
                    p = all_proxies[proxy_cursor % total]
                    proxy_cursor += 1
                    pid = proxy_id(p)
                    if pid in bad_ids or pid in in_use:
                        tried += 1
                        continue
                    if avoid_captcha and pid in captcha_blocked_ids:
                        tried += 1
                        continue
                    return p
                return None
            except Exception:
                log_exception("next_good_proxy")
                return None
            finally:
                pass

        pool: List[Optional[Dict]] = []

        if use_same_proxies and same_pool:
            for p in same_pool:
                if len(pool) >= TABS_COUNT:
                    break
                pool.append(p)
                in_use.add(proxy_id(p))
        elif len(good_pool) >= 24 and TABS_COUNT >= 24:
            try:
                good_target = min(12, len(good_pool), max(1, TABS_COUNT // 2))
                good_sample = random.sample(good_pool, good_target)
            except Exception:
                log_exception("initial_good_proxy_sample")
                good_sample = []

            chosen_ids = {proxy_id(p) for p in good_sample}
            remaining_slots = max(0, TABS_COUNT - len(good_sample))
            other_target = min(12, remaining_slots)

            other_candidates = [
                p for p in all_proxies if proxy_id(p) not in chosen_ids
            ]
            other_sample: List[Dict] = []
            if other_candidates and other_target > 0:
                try:
                    other_sample = random.sample(
                        other_candidates,
                        min(other_target, len(other_candidates)),
                    )
                except Exception:
                    log_exception("initial_other_proxy_sample")
                    other_sample = []

            initial_pool = good_sample + other_sample
            for p in initial_pool:
                if len(pool) >= TABS_COUNT:
                    break
                pool.append(p)
                in_use.add(proxy_id(p))

        while len(pool) < TABS_COUNT:
            p = next_good_proxy()
            if not p:
                break
            pool.append(p)
            in_use.add(proxy_id(p))

        if len(pool) < TABS_COUNT:
            print(
                f"[WARN] Нашлось только {len(pool)} пригодных прокси для {TABS_COUNT} вкладок."
            )
        if not pool:
            raise RuntimeError("Нет ни одного пригодного прокси для запуска.")

        async def create_context_and_page(proxy: Optional[Dict]) -> Tuple[BrowserContext, Page]:
            try:
                ctx = await browser.new_context(**context_args_for(proxy))
                if not (use_same_proxies and proxy and proxy_id(proxy) in same_proxy_ids_set):
                    await clear_cookies_once(ctx)
                page = await ctx.new_page()
                page.set_default_timeout(NAV_TIMEOUT)
                return ctx, page
            except Exception:
                log_exception("create_context_and_page")
                raise
            finally:
                pass

        contexts: List[BrowserContext] = []
        pages: List[Page] = []
        for idx, proxy in enumerate(pool):
            attempts = 0
            while True:
                try:
                    ctx, pg = await create_context_and_page(proxy)
                    contexts.append(ctx)
                    pages.append(pg)
                    break
                except Exception as exc:
                    attempts += 1
                    if proxy:
                        pid_bad = proxy_id(proxy)
                        in_use.discard(pid_bad)
                        bad_ids.add(pid_bad)
                        save_bad_proxy(proxy, f"context init failed: {exc}")
                    proxy = next_good_proxy(avoid_captcha=True)
                    if not proxy:
                        raise RuntimeError("No proxies left for context init.")
                    pool[idx] = proxy
                    in_use.add(proxy_id(proxy))
                    if attempts >= 3:
                        raise RuntimeError("Context init failed after retries.")

        async def replace_bad_tab(
            tab_i: int,
            bad_reason: str = "navigation failed",
            mark_as_bad: bool = True,
            avoid_captcha: bool = True,
        ) -> None:
            nonlocal pool, contexts, pages, in_use, bad_ids, captcha_blocked_ids
            try:
                old = pool[tab_i]
                if old:
                    pid_old = proxy_id(old)
                    in_use.discard(pid_old)
                    if mark_as_bad:
                        bad_ids.add(pid_old)
                        save_bad_proxy(old, bad_reason)
                    else:
                        captcha_blocked_ids.add(pid_old)

                try:
                    await contexts[tab_i].close()
                except Exception:
                    log_exception(f"replace_bad_tab.close_context[{tab_i}]")

                new_p = next_good_proxy(avoid_captcha=avoid_captcha)
                if not new_p:
                    raise RuntimeError("Закончились хорошие прокси — некого подставить.")

                pool[tab_i] = new_p
                in_use.add(proxy_id(new_p))

                try:
                    new_ctx, new_pg = await create_context_and_page(new_p)
                except Exception:
                    log_exception(f"replace_bad_tab.new_context[{tab_i}]")
                    raise

                contexts[tab_i] = new_ctx
                pages[tab_i] = new_pg
                log_line(
                    LOGS_DIR / "proxies_rotation.log",
                    f"[{time.strftime('%H:%M:%S')}] TAB#{tab_i} -> new proxy: {new_p} | reason={bad_reason}",
                )
            except Exception:
                log_exception(f"replace_bad_tab[{tab_i}]")
                raise
            finally:
                pass

        async def try_auto_solve_captcha_for_tab(
            page: Page,
            tab_idx: int,
            show_url: str,
            proxy_for_tab: Optional[Dict],
        ) -> Tuple[bool, str]:
            """
            Try solving Arkose FunCaptcha via 2Captcha and inject token into page.
            Returns (handled, message), where handled=True means token was applied.
            """
            nonlocal captcha_auto_disabled_reason
            if captcha_auto_disabled_reason:
                return False, captcha_auto_disabled_reason
            if not captcha_solver.is_active:
                LOGGER.info("captcha.auto disabled tab=%s", tab_idx)
                return False, "2captcha disabled"

            LOGGER.info("captcha.auto start tab=%s url=%s", tab_idx, (page.url or show_url))
            try:
                if await is_begin_captcha(page):
                    await focus_tab(page, tab_idx)
                    await click_begin_captcha(page)
                    await page.wait_for_timeout(1200)
            except Exception:
                log_exception(f"try_auto_solve.begin[TAB#{tab_idx}]")

            task = None
            for extract_try in range(1, 4):
                try:
                    task = await extract_funcaptcha_task(page, playwright_proxy=proxy_for_tab)
                except Exception:
                    log_exception(f"try_auto_solve.extract[TAB#{tab_idx}]")
                    task = None
                if task:
                    break
                # Begin -> challenge render can be delayed. Give Arkose a short settle window.
                if extract_try < 3:
                    try:
                        await page.wait_for_timeout(800 * extract_try)
                    except Exception:
                        pass

            if not task:
                # Fallback path for full-page visual challenge where pk/surl are hidden.
                try:
                    visual_ok, visual_msg = await solve_visual_funcaptcha_via_coordinates(
                        page, captcha_solver, logger=LOGGER
                    )
                except Exception:
                    log_exception(f"try_auto_solve.visual[TAB#{tab_idx}]")
                    visual_ok, visual_msg = False, "visual fallback exception"
                if visual_ok:
                    LOGGER.info("captcha.auto tab=%s visual fallback success: %s", tab_idx, visual_msg)
                    return True, visual_msg
                if _is_2captcha_fatal_error(visual_msg):
                    captcha_auto_disabled_reason = f"2captcha unavailable for this run: {visual_msg}"
                    LOGGER.error(
                        "captcha.auto disabled globally after fatal 2captcha error tab=%s reason=%s",
                        tab_idx,
                        captcha_auto_disabled_reason,
                    )
                    return False, captcha_auto_disabled_reason
                LOGGER.info("captcha.auto tab=%s visual fallback skipped: %s", tab_idx, visual_msg)

                try:
                    frame_urls = [
                        str(getattr(fr, "url", "")).strip()
                        for fr in getattr(page, "frames", [])
                        if str(getattr(fr, "url", "")).strip()
                    ][:8]
                except Exception:
                    frame_urls = []
                # Иногда после Arkose всплывает reCAPTCHA, которую можно обновить и продолжить.
                try:
                    refreshed = await try_reload_recaptcha(page)
                except Exception:
                    refreshed = False
                if refreshed:
                    LOGGER.info("captcha.auto tab=%s recaptcha reloaded", tab_idx)
                    return False, "recaptcha challenge reloaded"
                LOGGER.warning(
                    "captcha.auto tab=%s funcaptcha params not found page_url=%s frame_urls=%s",
                    tab_idx,
                    (page.url or show_url),
                    frame_urls,
                )
                return False, "funcaptcha params not found"

            if not task.page_url:
                task = task.__class__(
                    sitekey=task.sitekey,
                    page_url=show_url,
                    surl=task.surl,
                    blob=task.blob,
                    user_agent=task.user_agent,
                    proxy=task.proxy,
                )

            LOGGER.info(
                "captcha.auto solve tab=%s sitekey=%s surl=%s blob=%s proxy=%s",
                tab_idx,
                (task.sitekey[:8] + "...") if task.sitekey else "none",
                bool(task.surl),
                bool(task.blob),
                bool(task.proxy),
            )
            result = await captcha_solver.solve_funcaptcha(task)
            if not result.ok or not result.token:
                LOGGER.warning(
                    "captcha.auto failed tab=%s attempts=%s error=%s",
                    tab_idx,
                    result.attempts_used,
                    result.error,
                )
                if _is_2captcha_fatal_error(result.error):
                    captcha_auto_disabled_reason = (
                        f"2captcha unavailable for this run: {result.error}"
                    )
                    LOGGER.error(
                        "captcha.auto disabled globally after fatal 2captcha error tab=%s reason=%s",
                        tab_idx,
                        captcha_auto_disabled_reason,
                    )
                    return False, captcha_auto_disabled_reason
                return False, f"2captcha failed: {result.error}"

            token_applied = await apply_funcaptcha_token(page, result.token)
            if not token_applied:
                LOGGER.warning("captcha.auto token apply failed tab=%s", tab_idx)
                return False, "token apply failed"

            await page.wait_for_timeout(1200)
            msg = (
                f"2captcha solved"
                f" (id={result.captcha_id or 'n/a'}, attempts={result.attempts_used}, "
                f"sec={result.duration_seconds:.1f})"
            )
            LOGGER.info("captcha.auto success tab=%s %s", tab_idx, msg)
            return True, msg

        def compute_needed_carts(limit: int, target_total: int, tabs_used: int) -> int:
            try:
                if target_total and target_total > 0 and limit and limit > 0:
                    carts_needed = math.ceil(target_total / limit)
                    return min(tabs_used, carts_needed)
                return min(tabs_used, 1)
            except Exception:
                return min(tabs_used, 1)

        proxy_errors = (
            "ERR_PROXY_CONNECTION_FAILED",
            "ERR_TUNNEL_CONNECTION_FAILED",
            "Proxy Authentication Required",
            "407"
        )

        manual_first_show_done = False
        for show in todo:
            await run_ctx.mark_inflight(show["show_id"])

            url = show["url"]
            name = show["name"]
            try:
                target_total = int(show.get("target_total", 0)) if str(show.get("target_total","")).strip() else 0
            except Exception:
                target_total = 0
            max_per_order_val = str(show.get("max_per_order", "")).strip()
            per_order_fixed = int(max_per_order_val) if max_per_order_val.isdigit() else None

            def _parse_ticket_index(v) -> Optional[int]:
                if v is None:
                    return None
                s = str(v).strip()
                if s == "" or s.lower() == "nan":
                    return None
                try:
                    f = float(s); i = int(f)
                    if abs(f - i) < 1e-9:
                        return i
                except Exception:
                    pass
                m = re.search(r"-?\d+", s)
                return int(m.group(0)) if m else None

            ticket_index = _parse_ticket_index(show.get("ticket_index", ""))

            manual_active = manual_captcha_first_show and not manual_first_show_done
            active_pages_count = min(MANUAL_TAB_LIMIT, len(pages)) if manual_active else len(pages)

            nav_results: List[object] = [None] * active_pages_count
            batches = HUMAN_SCHEDULER.build_batches(active_pages_count)
            for batch_idx, batch in enumerate(batches):
                batch_tasks = [
                    open_show_on_page(pages[i], url, i) for i in batch
                ]
                batch_res = await asyncio.gather(
                    *batch_tasks, return_exceptions=True
                )
                for idx, res in zip(batch, batch_res):
                    nav_results[idx] = res
                if batch_idx < len(batches) - 1:
                    await HUMAN_SCHEDULER.pause_between_batches()
            for i, res in enumerate(nav_results):
                if isinstance(res, Exception) or res is False:
                    await replace_bad_tab(i, bad_reason=f"nav error: {res}")
                    ok = await open_show_on_page(pages[i], url, i)
                    if not ok:
                        await replace_bad_tab(i, bad_reason="nav error 2")
                        ok = await open_show_on_page(pages[i], url, i)
                        if not ok:
                            log_line(
                                LOGS_DIR / "errors.log",
                                f"[{time.strftime('%H:%M:%S')}] nav failed after replace; tab={i} url={url}",
                            )

            for i in range(active_pages_count):
                pg = pages[i]
                try:
                    bad_reason = await detect_bad_proxy_on_page(pg)
                except Exception:
                    bad_reason = None
                if bad_reason:
                    await replace_bad_tab(i, bad_reason=f"bad page: {bad_reason}")
                    ok = await open_show_on_page(pages[i], url, i)
                    if not ok:
                        await replace_bad_tab(i, bad_reason="nav after replace failed")
                        ok = await open_show_on_page(pages[i], url, i)
                        if not ok:
                            log_line(
                                LOGS_DIR / "errors.log",
                                f"[{time.strftime('%H:%M:%S')}] nav failed after bad-proxy replace; tab={i} url={url}",
                            )

            try:
                site_limit = await detect_per_order_limit_precise(pages[0], ticket_index)
            except Exception:
                site_limit = 8

            manual_tabs_limit = active_pages_count
            manual_insufficient_abort = False
            limit = per_order_fixed if (per_order_fixed is not None and per_order_fixed > 0) else site_limit
            if not limit:
                limit = 4
            tabs_used = manual_tabs_limit
            needed_carts_threshold = compute_needed_carts(limit, target_total, tabs_used)

            successes = 0
            attempts = 0
            notes: List[str] = []
            add_results: List[Tuple[int, bool, str]] = []
            early_soldout_abort = False
            early_ended_abort = False
            early_target_hit = False
            early_inventory_abort = False
            skip_until = -1
            captcha_tabs: set[int] = set()

            for i in range(manual_tabs_limit):
                pg = pages[i]
                if manual_active:
                    await focus_tab(pg, i)
                if (not manual_active) and target_total and (successes * limit) >= target_total:
                    early_target_hit = True
                    notes.append(
                        f"Ранний стоп до попытки: достигнут порог по билетам target_total={target_total} "
                        f"(оценочно собрано {successes * limit}, корзин {successes}, TAB#{i} пропущен)"
                    )
                    break
                if i <= skip_until:
                    continue

                try:
                    if await is_soldout_page(pg):
                        if manual_active:
                            notes.append(f"SOLD OUT manual stop: вкладка {i}")
                            notes.append("MANUAL_SOLD_OUT")
                            early_soldout_abort = True
                            manual_insufficient_abort = True
                            break
                        checks = []
                        for j in (i + 1, i + 2):
                            if j < manual_tabs_limit:
                                checks.append(await is_soldout_page(pages[j]))
                        if len(checks) == 2 and all(checks):
                            notes.append(f"SOLD OUT ранний выход: вкладки {i}, {i+1}, {i+2}")
                            early_soldout_abort = True
                            if manual_active:
                                manual_insufficient_abort = True
                            break
                        else:
                            add_results.append((i, False, "SOLD OUT / Not Available"))
                            attempts += 1
                            continue
                except Exception:
                    pass

                try:
                    if await is_ended_reliable(pg):
                        if manual_active:
                            notes.append(f"ENDED manual stop: вкладка {i}")
                            notes.append("MANUAL_ENDED")
                            early_ended_abort = True
                            manual_insufficient_abort = True
                            break
                        checks = []
                        for j in (i + 1, i + 2):
                            if j < manual_tabs_limit:
                                checks.append(await is_ended_reliable(pages[j]))
                        if len(checks) == 2 and all(checks):
                            notes.append(f"ENDED ранний выход: вкладки {i}, {i+1}, {i+2}")
                            early_ended_abort = True
                            if manual_active:
                                manual_insufficient_abort = True
                            break
                        else:
                            add_results.append((i, False, "ENDED"))
                            attempts += 1
                            continue
                except Exception:
                    pass

                await asyncio.sleep(random.randint(*ADD_SEQUENTIAL_DELAY) / 1000.0)

                try:
                    if manual_active and await is_cart_page(pg):
                        ok, msg = True, "OK (already in cart)"
                    else:
                        if manual_active:
                            try:
                                if await is_begin_captcha(pg):
                                    auto_ok, auto_msg = await try_auto_solve_captcha_for_tab(
                                        pg, i, url, pool[i]
                                    )
                                    if auto_ok:
                                        notes.append(f"TAB#{i}: {auto_msg}")
                                        ok_auto, msg_auto = await select_quantity_and_add(
                                            pg, limit, ticket_index
                                        )
                                        attempts += 1
                                        add_results.append(
                                            (i, ok_auto, f"{msg_auto} ({auto_msg})")
                                        )
                                        if ok_auto:
                                            successes += 1
                                            try:
                                                proxy_for_tab = pool[i]
                                                if proxy_for_tab:
                                                    pid = proxy_id(proxy_for_tab)
                                                    if pid not in captcha_blocked_ids:
                                                        save_good_proxy(proxy_for_tab)
                                            except Exception:
                                                log_exception(f"save_good_proxy.auto_begin[TAB#{i}]")
                                            continue
                                        notes.append(
                                            f"TAB#{i}: auto solve begin-path failed ({auto_msg})"
                                        )
                                        if is_inventory_message(str(auto_msg)):
                                            early_inventory_abort = True
                                            manual_insufficient_abort = True
                                            break

                                    await focus_tab(pg, i)
                                    await click_begin_captcha(pg)
                                    ok_retry, msg_retry, used_attempts, inv_abort = await wait_for_manual_cart(
                                        pg,
                                        i,
                                        url,
                                        limit,
                                        ticket_index,
                                        timeout_s=60,
                                        auto_solve_captcha=(
                                            lambda _pg=pg, _i=i, _url=url: try_auto_solve_captcha_for_tab(
                                                _pg, _i, _url, pool[_i]
                                            )
                                        ),
                                    )
                                    attempts += used_attempts
                                    if used_attempts:
                                        add_results.append((i, ok_retry, f"{msg_retry} (manual wait)"))
                                    if ok_retry:
                                        if used_attempts == 0:
                                            attempts += 1
                                            add_results.append((i, True, "OK (manual cart)"))
                                        successes += 1
                                        try:
                                            proxy_for_tab = pool[i]
                                            if proxy_for_tab:
                                                pid = proxy_id(proxy_for_tab)
                                                if pid not in captcha_blocked_ids:
                                                    save_good_proxy(proxy_for_tab)
                                        except Exception:
                                            log_exception(f"save_good_proxy.manual[TAB#{i}]")
                                    else:
                                        notes.append(f"TAB#{i}: manual wait failed ({msg_retry})")
                                        if not used_attempts:
                                            add_results.append((i, False, msg_retry))
                                    if inv_abort:
                                        early_inventory_abort = True
                                        manual_insufficient_abort = True
                                        break
                                    continue
                            except Exception:
                                log_exception(f"manual_begin_captcha[TAB#{i}]")
                        ok, msg = await select_quantity_and_add(pg, limit, ticket_index)
                except Exception as exc:
                    ok, msg = False, f"Exception: {exc}"
                    log_exception(f"select_quantity_and_add[TAB#{i}]")

                # фиксируем вкладки с капчей и сначала пробуем auto-solve (2Captcha)
                try:
                    is_captcha_here = False
                    if not ok:
                        is_captcha_here = await is_captcha(pg)
                    if is_captcha_here:
                        proxy_for_tab = pool[i]
                        auto_handled, auto_msg = await try_auto_solve_captcha_for_tab(
                            pg, i, url, proxy_for_tab
                        )

                        if auto_handled:
                            notes.append(f"TAB#{i}: {auto_msg}")
                            try:
                                ok_auto, msg_auto = await select_quantity_and_add(
                                    pg, limit, ticket_index
                                )
                            except Exception as exc:
                                ok_auto, msg_auto = False, f"Exception: {exc}"
                                log_exception(f"select_quantity_and_add.auto[TAB#{i}]")

                            attempts += 1
                            ok = ok_auto
                            msg = f"{msg_auto} ({auto_msg})"

                            captcha_still_here = False
                            if not ok:
                                try:
                                    captcha_still_here = await is_captcha(pg)
                                except Exception:
                                    captcha_still_here = False

                            if manual_active and captcha_still_here:
                                notes.append(f"TAB#{i}: CAPTCHA still present after auto solve")
                                ok_retry, msg_retry, used_attempts, inv_abort = await wait_for_manual_cart(
                                    pg,
                                    i,
                                    url,
                                    limit,
                                    ticket_index,
                                    timeout_s=60,
                                    auto_solve_captcha=(
                                        lambda _pg=pg, _i=i, _url=url: try_auto_solve_captcha_for_tab(
                                            _pg, _i, _url, pool[_i]
                                        )
                                    ),
                                )
                                attempts += used_attempts
                                if used_attempts:
                                    add_results.append((i, ok_retry, f"{msg_retry} (manual wait)"))
                                if ok_retry:
                                    if used_attempts == 0:
                                        attempts += 1
                                        add_results.append((i, True, "OK (manual cart)"))
                                    successes += 1
                                    try:
                                        if proxy_for_tab:
                                            pid = proxy_id(proxy_for_tab)
                                            if pid not in captcha_blocked_ids:
                                                save_good_proxy(proxy_for_tab)
                                    except Exception:
                                        log_exception(f"save_good_proxy.manual[TAB#{i}]")
                                else:
                                    notes.append(f"TAB#{i}: manual wait failed ({msg_retry})")
                                    if not used_attempts:
                                        add_results.append((i, False, msg_retry))
                                if inv_abort:
                                    early_inventory_abort = True
                                    manual_insufficient_abort = True
                                    break
                                continue

                            if (not manual_active) and captcha_still_here:
                                captcha_tabs.add(i)
                                if proxy_for_tab:
                                    captcha_blocked_ids.add(proxy_id(proxy_for_tab))
                                notes.append(
                                    f"TAB#{i}: CAPTCHA remains after auto solve, will retry with rotation"
                                )
                        else:
                            if manual_active:
                                notes.append(f"TAB#{i}: CAPTCHA detected (manual mode)")
                                ok_retry, msg_retry, used_attempts, inv_abort = await wait_for_manual_cart(
                                    pg,
                                    i,
                                    url,
                                    limit,
                                    ticket_index,
                                    timeout_s=60,
                                    auto_solve_captcha=(
                                        lambda _pg=pg, _i=i, _url=url: try_auto_solve_captcha_for_tab(
                                            _pg, _i, _url, pool[_i]
                                        )
                                    ),
                                )
                                attempts += used_attempts
                                if used_attempts:
                                    add_results.append((i, ok_retry, f"{msg_retry} (manual wait)"))
                                if ok_retry:
                                    if used_attempts == 0:
                                        attempts += 1
                                        add_results.append((i, True, "OK (manual cart)"))
                                    successes += 1
                                    try:
                                        if proxy_for_tab:
                                            pid = proxy_id(proxy_for_tab)
                                            if pid not in captcha_blocked_ids:
                                                save_good_proxy(proxy_for_tab)
                                    except Exception:
                                        log_exception(f"save_good_proxy.manual[TAB#{i}]")
                                else:
                                    notes.append(f"TAB#{i}: manual wait failed ({msg_retry})")
                                    if not used_attempts:
                                        add_results.append((i, False, msg_retry))
                                if inv_abort:
                                    early_inventory_abort = True
                                    manual_insufficient_abort = True
                                    break
                                continue

                            captcha_tabs.add(i)
                            if proxy_for_tab:
                                captcha_blocked_ids.add(proxy_id(proxy_for_tab))
                            notes.append(
                                f"TAB#{i}: CAPTCHA in main pass (auto solver: {auto_msg})"
                            )
                except Exception:
                    log_exception(f"is_captcha(main_pass)[TAB#{i}]")

                add_results.append((i, ok, msg))
                attempts += 1
                if ok:
                    successes += 1

                if ok:
                    try:
                        proxy_for_tab = pool[i]
                        if proxy_for_tab:
                            pid = proxy_id(proxy_for_tab)
                            if pid not in captcha_blocked_ids:
                                save_good_proxy(proxy_for_tab)
                    except Exception:
                        log_exception(f"save_good_proxy.main_pass[TAB#{i}]")

                if (not ok) and is_inventory_message(str(msg)):
                    notes.append(f"INVENTORY: ранний выход по первой вкладке TAB#{i}")
                    early_inventory_abort = True
                    if manual_active:
                        manual_insufficient_abort = True
                    break

                if (not manual_active) and target_total and (successes * limit) >= target_total:
                    early_target_hit = True
                    notes.append(
                        f"Ранний стоп: достигнут порог по билетам target_total={target_total} "
                        f"(оценочно собрано {successes * limit}, корзин={successes})"
                    )
                    break

            async def run_captcha_retries(
                problematic_tabs: List[int],
                target_total_local: int,
                limit_local: int,
                ticket_index_local: Optional[int],
                show_url: str,
            ) -> None:
                """
                Run post-pass retries for tabs where CAPTCHA was detected.

                Updates successes/attempts/add_results/notes in the outer scope.
                """
                nonlocal successes, attempts, add_results, notes
                try:
                    for tab_idx in problematic_tabs:
                        if target_total_local and (successes * limit_local) >= target_total_local:
                            break

                        attempt_for_tab = 0
                        use_same_proxy = True

                        while attempt_for_tab < 5:
                            if target_total_local and (successes * limit_local) >= target_total_local:
                                break

                            attempt_for_tab += 1

                            try:
                                if use_same_proxy:
                                    try:
                                        if not (
                                            use_same_proxies
                                            and pool[tab_idx]
                                            and proxy_id(pool[tab_idx]) in same_proxy_ids_set
                                        ):
                                            await clear_cookies_once(contexts[tab_idx])
                                    except Exception:
                                        log_exception(
                                            f"run_captcha_retries.clear_cookies[TAB#{tab_idx}]"
                                        )

                                    ok_nav = await open_show_on_page(
                                        pages[tab_idx], show_url, tab_idx
                                    )
                                    if not ok_nav:
                                        log_exception(
                                            f"run_captcha_retries.open_show_same_proxy[TAB#{tab_idx}]"
                                        )
                                        use_same_proxy = False
                                        continue
                                else:
                                    try:
                                        await replace_bad_tab(
                                            tab_idx,
                                            bad_reason="captcha retry",
                                            mark_as_bad=False,
                                            avoid_captcha=True,
                                        )
                                    except Exception:
                                        notes.append(
                                            f"TAB#{tab_idx}: не удалось заменить прокси при ретрае капчи"
                                        )
                                        log_exception(
                                            f"run_captcha_retries.replace_bad_tab[TAB#{tab_idx}]"
                                        )
                                        break

                                    ok_nav = await open_show_on_page(
                                        pages[tab_idx], show_url, tab_idx
                                    )
                                    if not ok_nav:
                                        log_exception(
                                            f"run_captcha_retries.open_show_new_proxy[TAB#{tab_idx}]"
                                        )
                                        continue

                                await asyncio.sleep(
                                    random.randint(*ADD_SEQUENTIAL_DELAY) / 1000.0
                                )

                                ok_retry, msg_retry = await select_quantity_and_add(
                                    pages[tab_idx],
                                    limit_local,
                                    ticket_index_local,
                                )
                                attempts += 1
                                add_results.append(
                                    (tab_idx, ok_retry, f"{msg_retry} (retry #{attempt_for_tab})")
                                )

                                if ok_retry:
                                    successes += 1
                                    try:
                                        proxy_for_tab = pool[tab_idx]
                                        if proxy_for_tab:
                                            pid = proxy_id(proxy_for_tab)
                                            if pid not in captcha_blocked_ids:
                                                save_good_proxy(proxy_for_tab)
                                    except Exception:
                                        log_exception(
                                            f"save_good_proxy.retry[TAB#{tab_idx}]"
                                        )
                                    notes.append(
                                        f"TAB#{tab_idx}: успешный ретрай после капчи "
                                        f"(попытка {attempt_for_tab})"
                                    )
                                    break

                                try:
                                    if await is_captcha(pages[tab_idx]):
                                        notes.append(
                                            f"TAB#{tab_idx}: CAPTCHA сохраняется после ретрая "
                                            f"(попытка {attempt_for_tab})"
                                        )
                                except Exception:
                                    log_exception(
                                        f"run_captcha_retries.is_captcha[TAB#{tab_idx}]"
                                    )

                                use_same_proxy = False
                            except Exception:
                                log_exception(f"run_captcha_retries.loop[TAB#{tab_idx}]")
                                use_same_proxy = False
                                continue
                except Exception:
                    log_exception("run_captcha_retries")

            if early_soldout_abort or early_ended_abort:
                est = successes * limit
                status = "SOLD OUT" if early_soldout_abort else "ENDED"
                if manual_active and manual_insufficient_abort:
                    notes.append(MANUAL_INSUFFICIENT_MARKER)
                if early_soldout_abort:
                    try:
                        safe_name = re.sub(r"[^A-Za-z0-9\s._-]+", "", name).strip()
                        date_tag = time.strftime("%d%m")
                        fname = SCREENS_DIR / f"{safe_name} {date_tag}.png"
                        await pages[0].screenshot(path=str(fname), full_page=True)
                    except Exception:
                        pass
                _row = {
                    "name": name,
                    "url": url,
                    "target_total": target_total,
                    "site_per_order_limit": site_limit,
                    "per_order_limit": limit,
                    "tabs_used": tabs_used,
                    "attempts": attempts,
                    "success_carts": successes,
                    "estimated_available_>=": est,
                    "needed_carts": needed_carts_threshold,
                    "status": status,
                    "ok_all": False,
                    "inv_errors": 0,
                    "other_fails": 0,
                    "notes": ("; ".join(notes)[:300] if notes else ""),
                }
                summary_rows.append(_row)
                await run_ctx.commit_done(show["show_id"], _row)
                if on_show_done:
                    await on_show_done(_row, len(summary_rows), total_valid)

                await asyncio.sleep(DELAY_BEFORE_CLEAR_CARTS_S)
                
                clear_tasks = []
                for pg in pages:
                    async def ctask(_pg=pg):
                        try:
                            await clear_cart(_pg)
                        except Exception:
                            pass
                    clear_tasks.append(ctask())
                await asyncio.gather(*clear_tasks)
                if manual_active:
                    manual_first_show_done = True
                continue

            if (
                not manual_active
                and
                target_total
                and (successes * limit) < target_total
                and captcha_tabs
                and not early_inventory_abort
            ):
                await run_captcha_retries(
                    sorted(captcha_tabs),
                    target_total,
                    limit,
                    ticket_index,
                    url,
                )

            if early_inventory_abort:
                inventory_exhausted = True
            else:
                inv_error_msgs = [m for (_i, ok, m) in add_results if not ok and is_inventory_message(m)]
                other_fail_msgs = [m for (_i, ok, m) in add_results if not ok and not is_inventory_message(m)]
                inventory_exhausted = (len(inv_error_msgs) > 0) and (successes < needed_carts_threshold)

            est = successes * limit

            if target_total and est >= target_total:
                status = "OK"
            elif successes >= needed_carts_threshold:
                status = "OK"
            elif inventory_exhausted:
                status = "КРАСНАЯ ЛИНИЯ (билетов не хватает)"
            else:
                status = "ЧАСТИЧНО (не все корзины набиты; без явных алертов)"

            ok_all = (successes == tabs_used)

            if manual_active and manual_insufficient_abort:
                notes.append(MANUAL_INSUFFICIENT_MARKER)

            _row = {
                "name": name,
                "url": url,
                "target_total": target_total,
                "site_per_order_limit": site_limit,
                "per_order_limit": limit,
                "tabs_used": tabs_used,
                "attempts": attempts,
                "success_carts": successes,
                "estimated_available_>=": est,
                "needed_carts": needed_carts_threshold,
                "status": status,
                "ok_all": ok_all,
                "inv_errors": (0 if early_inventory_abort else len(inv_error_msgs) if 'inv_error_msgs' in locals() else 0),
                "other_fails": (0 if early_inventory_abort else len(other_fail_msgs) if 'other_fail_msgs' in locals() else 0),
                "notes": ("; ".join(notes)[:300] if notes else ""),
            }
            summary_rows.append(_row)
            await run_ctx.commit_done(show["show_id"], _row)
            if on_show_done:
                await on_show_done(_row, len(summary_rows), total_valid)

            await asyncio.sleep(DELAY_BEFORE_CLEAR_CARTS_S)

            clear_tasks = []
            for pg in pages:
                async def ctask(_pg=pg):
                    try:
                        await clear_cart(_pg)
                    except Exception:
                        pass
                clear_tasks.append(ctask())
            await asyncio.gather(*clear_tasks)
            if manual_active:
                manual_first_show_done = True

        df = pd.DataFrame(summary_rows)
        if "url" in df.columns:
            df = df.drop(columns=["url"])
        df.to_csv("report.csv", index=False, encoding="utf-8-sig")
        await run_ctx.complete_run()
        try:
            save_last_good_proxy_ids([p for p in pool if p])
        except Exception:
            log_exception("save_last_good_proxy_ids.final")
        await browser.close()
        if manual_captcha_first_show:
            HEADLESS = True
        return summary_rows

if __name__ == "__main__":
    rows = asyncio.run(check_shows())
    for r in rows:
        print(f"{r.get('name') or r['url']}: {r['status']} (успешных корзин {r['success_carts']}/{r['tabs_used']})")
