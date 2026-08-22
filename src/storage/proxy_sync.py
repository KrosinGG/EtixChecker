"""Proxy synchronization service for pulling verified good proxies from remote sources (GitHub Raw / Gist)."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Set
import httpx

from src.utils.logger import LOGGER


class ProxySyncService:
    """Synchronizes good proxies list with remote GitHub Raw / Gist repository."""

    def __init__(
        self,
        local_file: Path = Path("data/good_proxies.txt"),
        sync_url: Optional[str] = None,
        timeout_s: float = 6.0,
    ) -> None:
        self.local_file = local_file
        self.sync_url = sync_url
        self.timeout_s = timeout_s

    def load_local_proxies(self) -> Set[str]:
        """Read unique valid proxies from local file."""
        if not self.local_file.exists():
            return set()
        try:
            return {
                line.strip().split("#")[0].strip()
                for line in self.local_file.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")
            }
        except Exception:
            return set()

    def save_local_proxies(self, proxies: Set[str]) -> None:
        """Write unique valid proxies to local file."""
        self.local_file.parent.mkdir(parents=True, exist_ok=True)
        cleaned = sorted([p.strip() for p in proxies if p.strip()])
        with open(self.local_file, "w", encoding="utf-8") as f:
            for p in cleaned:
                f.write(f"{p}\n")

    async def sync(self, custom_url: Optional[str] = None) -> List[str]:
        """
        Fetch remote good proxies, merge with local list, and save.
        """
        url = custom_url or self.sync_url
        local_set = self.load_local_proxies()

        if not url:
            return sorted(list(local_set))

        try:
            LOGGER.info(f"Syncing good proxies from remote source: {url}")
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    remote_lines = resp.text.splitlines()
                    remote_set = {
                        line.strip().split("#")[0].strip()
                        for line in remote_lines
                        if line.strip() and not line.strip().startswith("#")
                    }
                    merged = local_set.union(remote_set)
                    new_count = len(merged) - len(local_set)
                    self.save_local_proxies(merged)
                    LOGGER.info(
                        f"Proxy sync successful! Total: {len(merged)} proxies (added {new_count} new from remote)."
                    )
                    return sorted(list(merged))
                else:
                    LOGGER.warning(f"Remote proxy sync returned HTTP {resp.status_code}")
        except Exception as exc:
            LOGGER.warning(f"Could not sync good proxies from remote source: {exc}")

        return sorted(list(local_set))
