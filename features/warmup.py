"""Warm-up flow helpers."""

from __future__ import annotations

import random
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

WARMUP_SITES_FILE = Path("data") / "warmup_sites.txt"
URL_RE = re.compile(r"^https?://", flags=re.I)


EventCallback = Optional[Callable[[Dict[str, Any]], Awaitable[None]]]


def ensure_warmup_file(path: Path = WARMUP_SITES_FILE) -> None:
    """Create warm-up file template if it is missing."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# One URL per line\n"
        "https://www.wikipedia.org/\n"
        "https://www.reddit.com/\n"
        "https://news.ycombinator.com/\n",
        encoding="utf-8",
    )


def load_warmup_sites(path: Path = WARMUP_SITES_FILE) -> List[str]:
    """Load and validate warm-up URLs from text file."""
    ensure_warmup_file(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    sites: List[str] = []
    seen: set[str] = set()
    for line in lines:
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        if not URL_RE.match(item):
            continue
        if item in seen:
            continue
        seen.add(item)
        sites.append(item)
    return sites


def pick_warmup_urls(all_sites: Sequence[str], count: int) -> List[str]:
    """Pick up to `count` random URLs from the site list."""
    if count <= 0 or not all_sites:
        return []
    if len(all_sites) <= count:
        return list(all_sites)
    return random.sample(list(all_sites), count)


async def _emit_event(on_event: EventCallback, proxy_idx: int, message: str) -> None:
    if not on_event:
        return
    await on_event(
        {
            "kind": "warmup",
            "proxy_idx": proxy_idx,
            "message": message,
        }
    )


async def _scroll_for_seconds(page: Any, seconds: float) -> None:
    if seconds <= 0:
        return
    chunks = max(1, int(seconds * 4))
    delay_ms = int((seconds / chunks) * 1000)
    for _ in range(chunks):
        await page.mouse.wheel(0, random.randint(260, 520))
        await page.wait_for_timeout(delay_ms)


async def run_proxy_warmup(
    pages: Sequence[Any],
    urls: Sequence[str],
    per_proxy_count: int,
    nav_timeout_ms: int,
    on_event: EventCallback = None,
) -> None:
    """
    Perform one-time warm-up for each page/context.

    The warm-up opens random URLs, scrolls briefly and emits per-proxy events.
    """
    if not pages:
        return
    if not urls:
        for idx in range(len(pages)):
            await _emit_event(on_event, idx + 1, "warmup skipped (empty list)")
        return

    for idx, page in enumerate(pages):
        selected = pick_warmup_urls(urls, per_proxy_count)
        if not selected:
            await _emit_event(on_event, idx + 1, "warmup skipped")
            continue

        success_count = 0
        for site_url in selected:
            try:
                await page.goto(site_url, wait_until="domcontentloaded", timeout=nav_timeout_ms)
                await page.wait_for_timeout(random.randint(350, 900))
                await _scroll_for_seconds(page, random.uniform(1.8, 3.4))
                success_count += 1
            except Exception:
                continue

        if success_count == 0:
            await _emit_event(on_event, idx + 1, "warmup failed")
        else:
            await _emit_event(on_event, idx + 1, f"warmup ok ({success_count}/{len(selected)})")

