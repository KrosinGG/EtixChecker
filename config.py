from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Tuple


def _load_dotenv(path: Path = Path(".env")) -> None:
    """
    Minimal .env loader with override=False semantics.
    Existing OS env vars keep priority over .env values.
    """
    try:
        if not path.exists():
            return
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue

            if value and value[0] in {"'", '"'} and value[-1:] == value[0]:
                value = value[1:-1]

            # Remove inline comments for unquoted values: KEY=value # comment
            if "#" in value and not (raw_line.count('"') >= 2 or raw_line.count("'") >= 2):
                value = value.split("#", 1)[0].rstrip()

            os.environ.setdefault(key, value)
    except Exception:
        # Config loader should never crash the app on malformed .env.
        return


_load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    val = raw.strip().lower()
    if val in {"1", "true", "yes", "y", "on"}:
        return True
    if val in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except Exception:
        return default


def _env_range_ms(name: str, default: Tuple[int, int]) -> Tuple[int, int]:
    raw = os.getenv(name)
    if not raw:
        return default
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) != 2:
        return default
    try:
        lo = int(parts[0])
        hi = int(parts[1])
    except Exception:
        return default
    if hi < lo:
        lo, hi = hi, lo
    return lo, hi


@dataclass(frozen=True)
class AppConfig:
    headless: bool = False
    randomize_proxies: bool = True
    slowmo_ms: int = 80
    tabs_count: int = 8

    nav_timeout: int = 45000
    click_timeout: int = 20000
    batch_nav_delay_ms: Tuple[int, int] = (80, 160)
    after_click_sleep_ms: Tuple[int, int] = (600, 800)
    add_sequential_delay_ms: Tuple[int, int] = (500, 850)
    delay_before_clear_carts_s: int = 5
    strict_all_carts: bool = True

    bad_proxy_page_patterns: list[str] = field(
        default_factory=lambda: [
            r"ERR_PROXY_CONNECTION_FAILED",
            r"ERR_TUNNEL_CONNECTION_FAILED",
            r"Proxy Authentication Required",
            r"HTTP\s*ERROR\s*407",
            r"HTTP\s*ERROR\s*429",
            r"This site (can't|can't) be reached",
            r"ERR_CONNECTION_(TIMED_OUT|RESET|CLOSED)",
            r"ERR_NETWORK_CHANGED",
        ]
    )

    captcha_iframe_selectors: list[str] = field(
        default_factory=lambda: [
            "iframe[src*='recaptcha']",
            "iframe[title*='recaptcha']",
            "iframe[src*='hcaptcha.com']",
            "iframe[src*='challenges.cloudflare.com']",
        ]
    )
    captcha_element_selectors: list[str] = field(
        default_factory=lambda: [
            "[id*='captcha']",
            "[class*='captcha']",
        ]
    )
    captcha_text_patterns: list[str] = field(
        default_factory=lambda: [
            r"\bI[' ]?m not a robot\b",
            r"select all images",
            r"select all squares",
            r"verify you are a human",
            r"unusual traffic from your computer network",
        ]
    )

    sold_out_email_text: str = "We'll send you an email if tickets become available."
    sold_out_banner_selectors: list[str] = field(
        default_factory=lambda: [
            "div[role='alert']:has-text('This performance is sold out')",
            ".alert:has-text('This performance is sold out')",
            ".alert-info:has-text('This performance is sold out')",
            "div[role='alert']:has-text('SOLD OUT')",
            ".alert:has-text('SOLD OUT')",
            ".alert-info:has-text('SOLD OUT')",
        ]
    )
    sold_out_text_patterns: list[str] = field(
        default_factory=lambda: [
            r"\bsold\s*out\b",
            r"\bthis performance is sold out\b",
        ]
    )

    ended_selectors: list[str] = field(
        default_factory=lambda: [
            "div[role='alert']:has-text('Sales for this event have ended')",
            "div[role='alert']:has-text('Sales for this performance have ended')",
            ".alert:has-text('Sales for this event have ended')",
            ".alert:has-text('Sales for this performance have ended')",
            ".alert-info:has-text('Sales for this event have ended')",
            ".alert-info:has-text('Sales for this performance have ended')",
        ]
    )
    ended_text_patterns: list[str] = field(
        default_factory=lambda: [
            r"Sorry!\s*Sales\s*for\s*this\s*(event|performance)\s*have\s*ended",
            r"\bSales\s*for\s*this\s*(event|performance)\s*have\s*ended\b",
            r"\b(ticket\s*)?sales\s*(for this (event|performance)\s*)?have\s*ended\b",
            r"\bThis\s*(event|performance)\s*has\s*ended\b",
        ]
    )

    tracking_keys: set[str] = field(
        default_factory=lambda: {
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
            "gclid",
            "fbclid",
            "mc_cid",
            "mc_eid",
        }
    )

    @classmethod
    def from_env(cls) -> "AppConfig":
        base = cls()
        return cls(
            headless=_env_bool("ETIX_HEADLESS", base.headless),
            randomize_proxies=_env_bool("ETIX_RANDOMIZE_PROXIES", base.randomize_proxies),
            slowmo_ms=_env_int("ETIX_SLOWMO_MS", base.slowmo_ms),
            tabs_count=max(1, _env_int("ETIX_TABS_COUNT", base.tabs_count)),
            nav_timeout=_env_int("ETIX_NAV_TIMEOUT", base.nav_timeout),
            click_timeout=_env_int("ETIX_CLICK_TIMEOUT", base.click_timeout),
            batch_nav_delay_ms=_env_range_ms("ETIX_BATCH_NAV_DELAY_MS", base.batch_nav_delay_ms),
            after_click_sleep_ms=_env_range_ms("ETIX_AFTER_CLICK_SLEEP_MS", base.after_click_sleep_ms),
            add_sequential_delay_ms=_env_range_ms("ETIX_ADD_SEQUENTIAL_DELAY_MS", base.add_sequential_delay_ms),
            delay_before_clear_carts_s=_env_int(
                "ETIX_DELAY_BEFORE_CLEAR_CARTS_S", base.delay_before_clear_carts_s
            ),
            strict_all_carts=_env_bool("ETIX_STRICT_ALL_CARTS", base.strict_all_carts),
        )


def load_config() -> AppConfig:
    return AppConfig.from_env()
