"""Application configuration settings."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import List, Tuple


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal robust .env loader without external dependencies."""
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

            if "#" in value and not (raw_line.count('"') >= 2 or raw_line.count("'") >= 2):
                value = value.split("#", 1)[0].rstrip()

            os.environ.setdefault(key, value)
    except Exception:
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
    # AdsPower API Configuration
    adspower_api_url: str = os.getenv("ADSPOWER_API_URL", "http://127.0.0.1:50325")
    adspower_api_fallback_url: str = "http://local.adspower.net:50325"
    adspower_group_name: str = os.getenv("ADSPOWER_GROUP_NAME", "Inventory Etix (DO NOT TOUCH)")
    active_profiles_count: int = _env_int("ADSPOWER_ACTIVE_PROFILES_COUNT", 12)

    # Browser & Navigation timeouts
    headless: bool = _env_bool("ETIX_HEADLESS", False)
    slowmo_ms: int = _env_int("ETIX_SLOWMO_MS", 80)
    nav_timeout: int = _env_int("ETIX_NAV_TIMEOUT", 45000)
    click_timeout: int = _env_int("ETIX_CLICK_TIMEOUT", 20000)

    # Delays & Humanization
    batch_nav_delay_ms: Tuple[int, int] = _env_range_ms("ETIX_BATCH_NAV_DELAY_MS", (80, 160))
    after_click_sleep_ms: Tuple[int, int] = _env_range_ms("ETIX_AFTER_CLICK_SLEEP_MS", (600, 800))
    add_sequential_delay_ms: Tuple[int, int] = _env_range_ms("ETIX_ADD_SEQUENTIAL_DELAY_MS", (500, 850))
    delay_before_clear_carts_s: float = float(os.getenv("ETIX_DELAY_BEFORE_CLEAR_CARTS_S", "5.0"))
    strict_all_carts: bool = _env_bool("ETIX_STRICT_ALL_CARTS", True)

    # File paths
    data_dir: Path = Path("data")
    shows_csv: Path = Path("data/shows.csv")
    backup_dir: Path = Path("data/adspower_backup")
    good_proxies_file: Path = Path("data/good_proxies.txt")
    bad_proxies_file: Path = Path("data/bad_proxies.txt")
    runs_dir: Path = Path("runs")
    screens_dir: Path = Path("screens")
    logs_dir: Path = Path("logs")

    # Anti-bot and Sold Out Detection Selectors
    sold_out_banner_selectors: List[str] = field(
        default_factory=lambda: [
            "div[role='alert']:has-text('This performance is sold out')",
            ".alert:has-text('This performance is sold out')",
            ".alert-info:has-text('This performance is sold out')",
            "div[role='alert']:has-text('SOLD OUT')",
            ".alert:has-text('SOLD OUT')",
            ".alert-info:has-text('SOLD OUT')",
        ]
    )
    sold_out_text_patterns: List[str] = field(
        default_factory=lambda: [
            r"\bsold\s*out\b",
            r"\bthis performance is sold out\b",
        ]
    )

    ended_selectors: List[str] = field(
        default_factory=lambda: [
            "div[role='alert']:has-text('Sales for this event have ended')",
            "div[role='alert']:has-text('Sales for this performance have ended')",
            ".alert:has-text('Sales for this event have ended')",
            ".alert:has-text('Sales for this performance have ended')",
            ".alert-info:has-text('Sales for this event have ended')",
            ".alert-info:has-text('Sales for this performance have ended')",
        ]
    )
    ended_text_patterns: List[str] = field(
        default_factory=lambda: [
            r"Sorry!\s*Sales\s*for\s*this\s*(event|performance)\s*have\s*ended",
            r"\bSales\s*for\s*this\s*(event|performance)\s*have\s*ended\b",
            r"\b(ticket\s*)?sales\s*(for this (event|performance)\s*)?have\s*ended\b",
            r"\bThis\s*(event|performance)\s*has\s*ended\b",
        ]
    )

    # DataDome & Block Patterns
    blocked_text_patterns: List[str] = field(
        default_factory=lambda: [
            r"Access\s*Temporarily\s*Blocked",
            r"To help protect our platform from automated traffic",
            r"our security systems have temporarily restricted access",
        ]
    )
    slider_captcha_patterns: List[str] = field(
        default_factory=lambda: [
            r"Slide\s*right\s*to\s*secure\s*your\s*access",
            r"Please\s*confirm\s*you'?re\s*not\s*a\s*bot",
        ]
    )

    inventory_error_patterns: List[str] = field(
        default_factory=lambda: [
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
    )


CONFIG = AppConfig()
