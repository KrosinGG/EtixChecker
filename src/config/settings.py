from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple


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

            if key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass


_load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None:
        return default
    try:
        return int(val.strip())
    except ValueError:
        return default


def _env_range_ms(name: str, default: Tuple[int, int]) -> Tuple[int, int]:
    val = os.getenv(name)
    if not val:
        return default
    parts = val.split("-") if "-" in val else val.split(",")
    if len(parts) == 1:
        try:
            x = int(parts[0].strip())
            return x, x
        except ValueError:
            return default
    try:
        lo = int(parts[0].strip())
        hi = int(parts[1].strip())
    except ValueError:
        return default
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


# Absolute minimum safe bounds for delays (including +20ms safety margin)
# Users / UI cannot configure values lower than these thresholds.
MIN_SAFE_DELAYS: Dict[str, float] = {
    "batch_nav_delay_ms": 270.0,         # Base min 250ms + 20ms buffer
    "after_click_sleep_ms": 420.0,       # Base min 400ms + 20ms buffer (MUI state sync)
    "add_sequential_delay_ms": 520.0,    # Base min 500ms + 20ms buffer (Human action gap)
    "post_add_wait_ms": 1520.0,          # Base min 1500ms + 20ms buffer (Etix cart creation)
    "delay_before_clear_carts_s": 2.2,   # Base min 2.0s + 0.2s buffer (Hold confirmation)
    "clear_cart_stagger_ms": 320.0,      # Base min 300ms + 20ms buffer (Staggered clear)
}


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
    nav_timeout: int = _env_int("ETIX_NAV_TIMEOUT", 18000)
    click_timeout: int = _env_int("ETIX_CLICK_TIMEOUT", 20000)

    # Delays & Humanization (tuned for ~45s per 12 profiles with random jitter)
    batch_nav_delay_ms: Tuple[int, int] = _env_range_ms("ETIX_BATCH_NAV_DELAY_MS", (600, 1100))
    after_click_sleep_ms: Tuple[int, int] = _env_range_ms("ETIX_AFTER_CLICK_SLEEP_MS", (500, 900))
    add_sequential_delay_ms: Tuple[int, int] = _env_range_ms("ETIX_ADD_SEQUENTIAL_DELAY_MS", (1000, 1800))
    delay_before_clear_carts_s: float = float(os.getenv("ETIX_DELAY_BEFORE_CLEAR_CARTS_S", "4.0"))
    clear_cart_stagger_ms: Tuple[int, int] = _env_range_ms("ETIX_CLEAR_CART_STAGGER_MS", (600, 1000))
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
            r"over the per order limit",
            r"lead to over the per order limit",
            r"per order limit",
            r"not enough tickets",
            r"not enough adjacent seats",
            r"not enough tickets of that type available",
            r"change the type of tickets you are requesting",
            r"reduce the number of tickets and try again",
            r"\bPlease\s+reduce\b",
            r"\bChoose\s+fewer\b",
            r"exceeds maximum allowed",
            r"tickets are currently not available",
            r"quantity requested is not available",
            r"Выберите меньше",
            r"уменьш",
        ]
    )


def estimate_check_duration_seconds(profiles_count: int, config: AppConfig) -> float:
    """
    Estimate expected duration in seconds for checking one show with given number of profiles.
    """
    if profiles_count <= 0:
        return 0.0

    avg_nav_delay = sum(config.batch_nav_delay_ms) / 2000.0
    avg_add_delay = sum(config.add_sequential_delay_ms) / 2000.0
    avg_clear_delay = sum(config.clear_cart_stagger_ms) / 2000.0

    shifts = max(0, profiles_count - 1)
    nav_time = shifts * avg_nav_delay + 2.5
    add_time = shifts * avg_add_delay + 2.5
    hold_time = config.delay_before_clear_carts_s
    clear_time = shifts * avg_clear_delay + 1.2

    return round(nav_time + add_time + hold_time + clear_time, 1)


CONFIG = AppConfig()
