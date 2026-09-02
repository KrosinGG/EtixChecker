"""Update service for checking remote GitHub releases/commits and performing safe 1-click updates."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx

from src.utils.logger import LOGGER

GITHUB_REPO_OWNER = "KrosinGG"
GITHUB_REPO_NAME = "EtixChecker"
GITHUB_COMMITS_API = f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/commits/main"
GITHUB_ZIP_URL = f"https://github.com/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/archive/refs/heads/main.zip"

# Protected relative paths that must NEVER be overwritten or deleted during update
PROTECTED_PATHS: Set[str] = {
    ".env",
    "data/shows.csv",
    "data/good_proxies.txt",
    "data/bad_proxies.txt",
    "data/adspower_backup",
    "runs",
    "logs",
    "screens",
    "venv",
    "ms-playwright",
    ".git",
}


@dataclass
class VersionInfo:
    sha: str
    short_sha: str
    message: str
    author: str
    date: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "sha": self.sha,
            "short_sha": self.short_sha,
            "message": self.message,
            "author": self.author,
            "date": self.date,
        }


class UpdateService:
    """Handles version checks against GitHub and safe local updates."""

    def __init__(self, root_dir: Optional[Path] = None) -> None:
        self.root_dir = root_dir or Path.cwd()
        self.version_file = self.root_dir / ".version"

    def get_local_version(self) -> Optional[VersionInfo]:
        """Get local commit version from Git or .version metadata file."""
        # 1. Try via Git CLI
        if (self.root_dir / ".git").exists():
            try:
                res = subprocess.run(
                    ["git", "log", "-1", "--format=%H|%h|%s|%an|%cd"],
                    cwd=str(self.root_dir),
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=5,
                )
                output = res.stdout.strip()
                if output and "|" in output:
                    parts = output.split("|", 4)
                    return VersionInfo(
                        sha=parts[0],
                        short_sha=parts[1],
                        message=parts[2] if len(parts) > 2 else "",
                        author=parts[3] if len(parts) > 3 else "",
                        date=parts[4] if len(parts) > 4 else "",
                    )
            except Exception as exc:
                LOGGER.debug(f"Git log failed: {exc}")

        # 2. Try via .version file
        if self.version_file.exists():
            try:
                data = json.loads(self.version_file.read_text(encoding="utf-8"))
                return VersionInfo(
                    sha=data.get("sha", ""),
                    short_sha=data.get("short_sha", data.get("sha", "")[:7]),
                    message=data.get("message", ""),
                    author=data.get("author", ""),
                    date=data.get("date", ""),
                )
            except Exception as exc:
                LOGGER.debug(f"Read .version failed: {exc}")

        return None

    async def check_for_updates(self) -> Tuple[bool, Optional[VersionInfo], Optional[str]]:
        """
        Check GitHub API for latest commit on main branch.
        Returns: (has_update, remote_version_info, error_message)
        """
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "EtixChecker-Updater/2026",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(GITHUB_COMMITS_API, headers=headers)
                if resp.status_code != 200:
                    return False, None, f"GitHub API вернул статус {resp.status_code}: {resp.text[:150]}"

                data = resp.json()
                sha = data.get("sha", "")
                commit_dict = data.get("commit", {})
                message = commit_dict.get("message", "").split("\n")[0]
                author = commit_dict.get("author", {}).get("name", "Unknown")
                date = commit_dict.get("author", {}).get("date", "")

                remote_info = VersionInfo(
                    sha=sha,
                    short_sha=sha[:7] if sha else "",
                    message=message,
                    author=author,
                    date=date,
                )

                local_info = self.get_local_version()
                if local_info is None:
                    # No local version info -> suggest update
                    return True, remote_info, None

                # If local SHA is different from remote SHA -> update available
                has_update = (local_info.sha.lower() != remote_info.sha.lower())
                return has_update, remote_info, None

        except Exception as exc:
            LOGGER.error(f"Error checking GitHub for updates: {exc}")
            return False, None, f"Не удалось связаться с сервером GitHub: {exc}"

    def _is_path_protected(self, rel_path_str: str) -> bool:
        """Check if relative path matches any protected files/directories."""
        norm = rel_path_str.replace("\\", "/").strip("/")
        for prot in PROTECTED_PATHS:
            prot_norm = prot.replace("\\", "/").strip("/")
            if norm == prot_norm or norm.startswith(f"{prot_norm}/"):
                return True
        return False

    async def apply_update(self, remote_version: Optional[VersionInfo] = None) -> Tuple[bool, str]:
        """
        Download latest main.zip or perform git pull, safely updating files
        without touching protected configuration and data files.
        """
        # 1. Strategy: If git is initialized and available
        if (self.root_dir / ".git").exists():
            try:
                LOGGER.info("Attempting update via Git...")
                # Stash changes to protected files if any
                subprocess.run(["git", "fetch", "origin", "main"], cwd=str(self.root_dir), capture_output=True, timeout=15)
                res = subprocess.run(
                    ["git", "pull", "--no-rebase", "origin", "main"],
                    cwd=str(self.root_dir),
                    capture_output=True,
                    text=True,
                    timeout=25,
                )
                if res.returncode == 0:
                    LOGGER.info("Git pull completed successfully.")
                    self._update_dependencies_if_needed()
                    return True, "Файлы программы успешно обновлены через Git!"
            except Exception as exc:
                LOGGER.warning(f"Git pull failed, falling back to ZIP download: {exc}")

        # 2. Strategy: ZIP Download fallback
        LOGGER.info(f"Downloading update ZIP from {GITHUB_ZIP_URL}...")
        temp_dir = Path(tempfile.mkdtemp(prefix="etix_update_"))
        zip_path = temp_dir / "repo.zip"
        extract_dir = temp_dir / "extracted"

        try:
            async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
                resp = await client.get(GITHUB_ZIP_URL)
                if resp.status_code != 200:
                    return False, f"Не удалось скачать архив с GitHub (код {resp.status_code})"
                zip_path.write_bytes(resp.content)

            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)

            # Find root folder in zip (e.g. EtixChecker-main)
            subdirs = [d for d in extract_dir.iterdir() if d.is_dir()]
            source_dir = subdirs[0] if subdirs else extract_dir

            # Recursively copy updated files while skipping protected paths
            updated_count = 0
            for root, dirs, files in os.walk(source_dir):
                rel_root = Path(root).relative_to(source_dir)

                for f in files:
                    src_file = Path(root) / f
                    rel_file = rel_root / f
                    rel_str = str(rel_file).replace("\\", "/")

                    if self._is_path_protected(rel_str):
                        LOGGER.debug(f"Skipping protected file: {rel_str}")
                        continue

                    dest_file = self.root_dir / rel_file
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, dest_file)
                    updated_count += 1

            LOGGER.info(f"Successfully updated {updated_count} files.")

            # Save version info
            if remote_version:
                self.version_file.write_text(
                    json.dumps(remote_version.to_dict(), indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

            # Update dependencies if needed
            self._update_dependencies_if_needed()

            return True, f"Успешно обновлено {updated_count} файлов проекта!"

        except Exception as exc:
            LOGGER.error(f"Failed to apply update: {exc}")
            return False, f"Ошибка при установке обновления: {exc}"
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _update_dependencies_if_needed(self) -> None:
        """Run pip install -r requirements.txt if venv is present."""
        venv_pip = self.root_dir / "venv" / "Scripts" / "pip.exe"
        req_file = self.root_dir / "requirements.txt"
        if venv_pip.exists() and req_file.exists():
            try:
                LOGGER.info("Updating Python dependencies in venv...")
                subprocess.run(
                    [str(venv_pip), "install", "-r", str(req_file), "-q"],
                    cwd=str(self.root_dir),
                    capture_output=True,
                    timeout=60,
                )
            except Exception as exc:
                LOGGER.warning(f"Dependency update check failed: {exc}")
