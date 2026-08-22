"""AdsPower profile manager for active and reserve allocation with dynamic proxy updates."""

from __future__ import annotations

import random
import re
from pathlib import Path
from typing import Dict, List, Optional, Set

from src.adspower.backup_service import ProfileBackupService
from src.adspower.client import AdsPowerClient
from src.domain.enums import ProfileRole
from src.domain.models import AdsPowerProfile
from src.utils.logger import LOGGER


class AdsPowerProfileManager:
    """Manages active (12) and reserve profiles, good proxies tracking and failovers."""

    def __init__(
        self,
        client: AdsPowerClient,
        backup_service: Optional[ProfileBackupService] = None,
        good_proxies_file: Path = Path("data/good_proxies.txt"),
        bad_proxies_file: Path = Path("data/bad_proxies.txt"),
    ) -> None:
        self.client = client
        self.backup_service = backup_service or ProfileBackupService()
        self.good_proxies_file = good_proxies_file
        self.bad_proxies_file = bad_proxies_file
        self.profiles: List[AdsPowerProfile] = []
        self._good_proxies: Set[str] = self._load_proxy_list(good_proxies_file)
        self._bad_proxies: Set[str] = self._load_proxy_list(bad_proxies_file)
        self._session_bad_proxies: Set[str] = set()  # Bad proxies strictly for current run cycle

    def _load_proxy_list(self, file_path: Path) -> Set[str]:
        if not file_path.exists():
            return set()
        try:
            return {
                line.strip().split("#")[0].strip()
                for line in file_path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")
            }
        except Exception:
            return set()

    def record_good_proxy(self, proxy_str: str) -> None:
        """Add proxy to good_proxies.txt and in-memory set."""
        proxy_str = proxy_str.strip()
        if not proxy_str:
            return
        if proxy_str not in self._good_proxies:
            self._good_proxies.add(proxy_str)
            self.good_proxies_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.good_proxies_file, "a", encoding="utf-8") as f:
                f.write(f"{proxy_str}\n")
            LOGGER.info(f"Recorded working good proxy: {proxy_str}")

    def record_bad_proxy(self, proxy_str: str, reason: str = "") -> None:
        """
        Record proxy as bad strictly for this current check cycle (in session).
        Also persists to bad_proxies.txt for history.
        """
        proxy_str = proxy_str.strip()
        if not proxy_str:
            return
        self._session_bad_proxies.add(proxy_str)
        self._bad_proxies.add(proxy_str)
        self.bad_proxies_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.bad_proxies_file, "a", encoding="utf-8") as f:
                f.write(f"{proxy_str} # {reason}\n")
        except Exception:
            pass
        LOGGER.warning(f"Marked bad proxy for current cycle: {proxy_str} ({reason})")

    def is_proxy_bad_in_session(self, proxy_str: str) -> bool:
        """Check if proxy is marked bad in current run cycle."""
        return proxy_str.strip() in self._session_bad_proxies

    def get_good_proxies_list(self) -> List[str]:
        """Get all known working proxies excluding those failed in this session."""
        self._good_proxies = self._load_proxy_list(self.good_proxies_file)
        return [p for p in self._good_proxies if p not in self._session_bad_proxies]

    def get_random_good_proxy(self) -> Optional[str]:
        """Pick a random clean working proxy."""
        clean = self.get_good_proxies_list()
        if clean:
            return random.choice(clean)
        return None

    async def load_and_organize_profiles(
        self,
        group_name: str = "Inventory Etix (DO NOT TOUCH)",
        active_count: int = 12,
    ) -> List[AdsPowerProfile]:
        """
        Fetch profiles from AdsPower, backup metadata, sort, and allocate active vs reserve.
        """
        raw_list = await self.client.get_profiles_by_group(group_name=group_name)
        if not raw_list:
            LOGGER.warning(f"No profiles found in AdsPower for group '{group_name}'!")
            return []

        # Local backup of metadata
        self.backup_service.backup_profiles(group_name, raw_list)

        parsed: List[AdsPowerProfile] = []
        for item in raw_list:
            proxy_cfg = item.get("user_proxy_config", {})
            profile = AdsPowerProfile(
                user_id=str(item.get("user_id", "")),
                name=str(item.get("name", "")),
                serial_number=str(item.get("serial_number", "")),
                group_id=str(item.get("group_id", "")),
                group_name=group_name,
                proxy_host=str(proxy_cfg.get("proxy_host", "")),
                proxy_port=str(proxy_cfg.get("proxy_port", "")),
                proxy_user=str(proxy_cfg.get("proxy_user", "")),
                proxy_password=str(proxy_cfg.get("proxy_password", "")),
                proxy_type=str(proxy_cfg.get("proxy_type", "http")),
                raw_data=item,
            )
            parsed.append(profile)

        def _sort_key(p: AdsPowerProfile) -> int:
            nums = re.findall(r"\d+", p.name)
            if nums:
                return int(nums[0])
            nums_serial = re.findall(r"\d+", p.serial_number)
            if nums_serial:
                return int(nums_serial[0])
            return 999999

        parsed.sort(key=_sort_key)

        for idx, prof in enumerate(parsed):
            if idx < active_count:
                prof.role = ProfileRole.ACTIVE
            else:
                prof.role = ProfileRole.RESERVE

        self.profiles = parsed
        active = [p for p in self.profiles if p.role == ProfileRole.ACTIVE]
        reserve = [p for p in self.profiles if p.role == ProfileRole.RESERVE]
        LOGGER.info(
            f"Loaded {len(parsed)} profiles from AdsPower (Active: {len(active)}, Reserve: {len(reserve)})"
        )
        return self.profiles

    def get_active_profiles(self) -> List[AdsPowerProfile]:
        return [p for p in self.profiles if p.role == ProfileRole.ACTIVE]

    def get_reserve_profiles(self) -> List[AdsPowerProfile]:
        return [p for p in self.profiles if p.role == ProfileRole.RESERVE]

    def get_next_available_reserve(self) -> Optional[AdsPowerProfile]:
        """Get an unallocated reserve profile."""
        reserves = [p for p in self.profiles if p.role == ProfileRole.RESERVE]
        if reserves:
            # Pick randomly from reserve pool
            return random.choice(reserves)
        return None

    async def setup_reserve_profile_with_good_proxy(
        self,
        reserve_profile: AdsPowerProfile,
        good_proxy_str: Optional[str] = None,
    ) -> bool:
        """
        Update reserve profile proxy via AdsPower API before launching.
        """
        proxy_str = good_proxy_str or self.get_random_good_proxy()
        if not proxy_str:
            LOGGER.warning("No clean good proxy available to assign to reserve profile.")
            return False

        parsed_cfg = self.client.parse_proxy_string(proxy_str)
        if not parsed_cfg:
            LOGGER.error(f"Failed to parse proxy string: {proxy_str}")
            return False

        ok = await self.client.update_profile_proxy(reserve_profile.user_id, parsed_cfg)
        if ok:
            reserve_profile.proxy_host = parsed_cfg["proxy_host"]
            reserve_profile.proxy_port = parsed_cfg["proxy_port"]
            reserve_profile.proxy_user = parsed_cfg["proxy_user"]
            reserve_profile.proxy_password = parsed_cfg["proxy_password"]
            reserve_profile.proxy_type = parsed_cfg["proxy_type"]
            LOGGER.info(
                f"Assigned good proxy {reserve_profile.proxy_key} to reserve profile {reserve_profile.name} ({reserve_profile.user_id})"
            )
            return True
        return False
