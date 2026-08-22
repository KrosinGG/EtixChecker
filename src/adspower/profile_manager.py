"""AdsPower profile manager for active and reserve allocation."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Set
import re

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

    def _load_proxy_list(self, file_path: Path) -> Set[str]:
        if not file_path.exists():
            return set()
        try:
            return {
                line.strip()
                for line in file_path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")
            }
        except Exception:
            return set()

    def record_good_proxy(self, proxy_str: str) -> None:
        """Add proxy to good_proxies.txt."""
        proxy_str = proxy_str.strip()
        if not proxy_str or proxy_str in self._good_proxies:
            return
        self._good_proxies.add(proxy_str)
        self.good_proxies_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.good_proxies_file, "a", encoding="utf-8") as f:
            f.write(f"{proxy_str}\n")
        LOGGER.info(f"Recorded working good proxy: {proxy_str}")

    def record_bad_proxy(self, proxy_str: str, reason: str = "") -> None:
        """Add proxy to bad_proxies.txt."""
        proxy_str = proxy_str.strip()
        if not proxy_str or proxy_str in self._bad_proxies:
            return
        self._bad_proxies.add(proxy_str)
        self.bad_proxies_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.bad_proxies_file, "a", encoding="utf-8") as f:
            f.write(f"{proxy_str} # {reason}\n")
        LOGGER.warning(f"Recorded bad proxy: {proxy_str} ({reason})")

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

        # Sort profiles numerically by name/serial if possible (e.g. 1..12)
        def _sort_key(p: AdsPowerProfile) -> int:
            nums = re.findall(r"\d+", p.name)
            if nums:
                return int(nums[0])
            nums_serial = re.findall(r"\d+", p.serial_number)
            if nums_serial:
                return int(nums_serial[0])
            return 999999

        parsed.sort(key=_sort_key)

        # Assign roles: first `active_count` are ACTIVE, remaining are RESERVE
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

    def swap_failing_profile(self, failing_user_id: str, reason: str = "") -> Optional[AdsPowerProfile]:
        """Replace a failing active profile with a healthy reserve profile."""
        failing_prof = next((p for p in self.profiles if p.user_id == failing_user_id), None)
        if failing_prof:
            failing_prof.role = ProfileRole.DISABLED
            if failing_prof.proxy_key:
                self.record_bad_proxy(failing_prof.proxy_key, reason)

        reserve_prof = next((p for p in self.profiles if p.role == ProfileRole.RESERVE), None)
        if reserve_prof:
            reserve_prof.role = ProfileRole.ACTIVE
            LOGGER.info(
                f"Swapped failed profile {failing_user_id} with reserve profile {reserve_prof.user_id} ({reserve_prof.name})"
            )
            return reserve_prof

        LOGGER.warning(f"No reserve profiles available to swap {failing_user_id}!")
        return None
