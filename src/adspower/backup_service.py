"""Backup and restore service for AdsPower profile metadata."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from src.utils.logger import LOGGER

DEFAULT_BACKUP_DIR = Path("data/adspower_backup")


class ProfileBackupService:
    """Saves and restores profile configurations locally on PC."""

    def __init__(self, backup_dir: Path = DEFAULT_BACKUP_DIR) -> None:
        self.backup_dir = backup_dir
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.latest_backup_file = self.backup_dir / "profiles_backup.json"

    def backup_profiles(self, group_name: str, raw_profiles: List[Dict[str, Any]]) -> Path:
        """Create timestamped and latest backup of AdsPower profiles."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        timestamped_file = self.backup_dir / f"profiles_{timestamp}.json"

        payload = {
            "backup_date": datetime.now().isoformat(),
            "group_name": group_name,
            "total_profiles": len(raw_profiles),
            "profiles": raw_profiles,
        }

        content = json.dumps(payload, ensure_ascii=False, indent=2)
        timestamped_file.write_text(content, encoding="utf-8")
        self.latest_backup_file.write_text(content, encoding="utf-8")

        LOGGER.info(
            f"Saved local backup of {len(raw_profiles)} profiles for group '{group_name}' "
            f"to {timestamped_file}"
        )
        return timestamped_file

    def load_latest_backup(self) -> Optional[Dict[str, Any]]:
        """Load latest local backup if available."""
        if not self.latest_backup_file.exists():
            return None
        try:
            return json.loads(self.latest_backup_file.read_text(encoding="utf-8"))
        except Exception as exc:
            LOGGER.error(f"Failed to read latest profiles backup: {exc}")
            return None
