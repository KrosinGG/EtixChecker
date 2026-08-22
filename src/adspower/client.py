"""AdsPower Local API asynchronous client."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
import httpx

from src.utils.logger import LOGGER


class AdsPowerClient:
    """Client for AdsPower Local REST API."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:50325",
        fallback_url: str = "http://local.adspower.net:50325",
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.fallback_url = fallback_url.rstrip("/")
        self.timeout = timeout
        self._active_base_url: Optional[str] = self.base_url

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send HTTP request with automatic URL fallback."""
        urls_to_try = [self.base_url, self.fallback_url]
        if self._active_base_url and self._active_base_url in urls_to_try:
            urls_to_try.remove(self._active_base_url)
            urls_to_try.insert(0, self._active_base_url)

        last_err: Optional[Exception] = None
        for b_url in urls_to_try:
            full_url = f"{b_url}{endpoint}"
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.request(
                        method=method,
                        url=full_url,
                        params=params,
                        json=json_data,
                    )
                    data = resp.json()
                    if data.get("code") == 0:
                        self._active_base_url = b_url
                    return data
            except Exception as exc:
                last_err = exc
                continue

        LOGGER.error(f"AdsPower API request failed for {endpoint}: {last_err}")
        return {"code": -1, "msg": f"Connection failed: {last_err}", "data": {}}

    async def check_status(self) -> bool:
        """Check if AdsPower Local API is alive."""
        res = await self._request("GET", "/status")
        return res.get("code") == 0

    async def get_groups(self, page_size: int = 100) -> List[Dict[str, Any]]:
        """Get all profile groups."""
        res = await self._request("GET", "/api/v1/group/list", params={"page_size": page_size})
        if res.get("code") == 0:
            return res.get("data", {}).get("list", [])
        return []

    async def find_group_id_by_name(
        self,
        group_name: str,
        cached_groups: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[str]:
        """Find group_id by exact or partial name match."""
        groups = cached_groups if cached_groups is not None else await self.get_groups()
        target = group_name.strip().lower()
        for g in groups:
            g_name = g.get("group_name", "").strip().lower()
            if g_name == target or target in g_name:
                return str(g.get("group_id"))
        return None

    async def get_profiles_by_group(
        self,
        group_id: Optional[str] = None,
        group_name: Optional[str] = None,
        page_size: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get all profiles in a given group."""
        if not group_id and group_name:
            group_id = await self.find_group_id_by_name(group_name)

        params: Dict[str, Any] = {"page_size": page_size}
        if group_id:
            params["group_id"] = group_id

        res = await self._request("GET", "/api/v1/user/list", params=params)
        if res.get("code") == 0:
            return res.get("data", {}).get("list", [])
        return []

    async def start_browser(
        self,
        user_id: str,
        open_tabs: int = 1,
        headless: bool = False,
    ) -> Optional[str]:
        """
        Start browser for profile user_id and return WebSocket debugging URL.
        """
        params: Dict[str, Any] = {
            "user_id": user_id,
            "open_tabs": open_tabs,
        }
        if headless:
            params["headless"] = "1"

        res = await self._request("GET", "/api/v1/browser/start", params=params)
        if res.get("code") == 0:
            ws_puppeteer = res.get("data", {}).get("ws", {}).get("puppeteer")
            if ws_puppeteer:
                return str(ws_puppeteer)
        LOGGER.warning(f"Failed to start AdsPower browser for {user_id}: {res.get('msg')}")
        return None

    async def stop_browser(self, user_id: str) -> bool:
        """Stop browser for profile user_id."""
        res = await self._request("GET", "/api/v1/browser/stop", params={"user_id": user_id})
        return res.get("code") == 0

    async def get_active_browsers(self) -> List[str]:
        """Get list of active browser user_ids."""
        res = await self._request("GET", "/api/v1/browser/active")
        if res.get("code") == 0:
            return res.get("data", {}).get("list", [])
        return []

    async def stop_all_active_browsers(self) -> int:
        """Stop all currently open AdsPower browsers."""
        active = await self.get_active_browsers()
        count = 0
        for uid in active:
            if await self.stop_browser(uid):
                count += 1
        return count
