"""Persistent proxy state helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

DATA_DIR = Path("data")
STATE_DIR = DATA_DIR / "proxy_state"
LAST_GOOD_PROXIES_FILE = DATA_DIR / "last_good_proxies.json"


def make_proxy_id(proxy: Dict[str, Any]) -> str:
    """Build a stable ID for a proxy definition."""
    return f"{proxy.get('server', '')}|{proxy.get('username', '')}"


def _proxy_state_hash(proxy_id: str) -> str:
    digest = hashlib.sha256(proxy_id.encode("utf-8")).hexdigest()
    return digest[:24]


def proxy_state_path(proxy: Optional[Dict[str, Any]]) -> Path:
    """Resolve state file path for a specific proxy."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not proxy:
        return STATE_DIR / "direct_connection.json"
    return STATE_DIR / f"{_proxy_state_hash(make_proxy_id(proxy))}.json"


def existing_state_path(proxy: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return existing state path string if it exists, otherwise None."""
    path = proxy_state_path(proxy)
    return str(path) if path.exists() else None


def has_state(proxy: Optional[Dict[str, Any]]) -> bool:
    """Check whether persistent state exists for proxy."""
    return proxy_state_path(proxy).exists()


async def save_context_state(context: Any, proxy: Optional[Dict[str, Any]]) -> None:
    """Persist browser context storage state for the proxy."""
    path = proxy_state_path(proxy)
    await context.storage_state(path=str(path))


async def save_states_batch(contexts: Iterable[Any], proxies: Iterable[Optional[Dict[str, Any]]]) -> None:
    """Persist storage states for all context/proxy pairs."""
    for context, proxy in zip(contexts, proxies):
        try:
            await save_context_state(context, proxy)
        except Exception:
            continue


def load_last_good_proxy_ids() -> List[str]:
    """Load ordered list of last good proxy IDs."""
    if not LAST_GOOD_PROXIES_FILE.exists():
        return []
    try:
        raw = json.loads(LAST_GOOD_PROXIES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


def save_last_good_proxy_ids(proxies: Iterable[Optional[Dict[str, Any]]]) -> None:
    """Save ordered proxy IDs from the latest run."""
    ids: List[str] = []
    seen: set[str] = set()
    for proxy in proxies:
        if not proxy:
            continue
        proxy_id = make_proxy_id(proxy)
        if proxy_id in seen:
            continue
        ids.append(proxy_id)
        seen.add(proxy_id)
    LAST_GOOD_PROXIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_GOOD_PROXIES_FILE.write_text(
        json.dumps(ids, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

