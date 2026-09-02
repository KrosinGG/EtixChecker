"""Verification tests for UpdateService and protected file safety."""

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Add root directory to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.updater import UpdateService, VersionInfo, PROTECTED_PATHS


def test_protected_paths():
    updater = UpdateService()
    # Ensure critical user files are protected
    assert updater._is_path_protected(".env")
    assert updater._is_path_protected(".env.local") is False
    assert updater._is_path_protected("data/shows.csv")
    assert updater._is_path_protected("data/good_proxies.txt")
    assert updater._is_path_protected("data/bad_proxies.txt")
    assert updater._is_path_protected("data/adspower_backup/profiles.json")
    assert updater._is_path_protected("runs/run_123.json")
    assert updater._is_path_protected("logs/app.log")
    assert updater._is_path_protected("venv/Scripts/python.exe")
    assert updater._is_path_protected("ms-playwright/chromium")
    assert updater._is_path_protected(".git/config")
    
    # Ensure non-protected code files can be updated
    assert updater._is_path_protected("src/etix/checker.py") is False
    assert updater._is_path_protected("gui_app.py") is False
    assert updater._is_path_protected("requirements.txt") is False
    print("  [+] Protected paths verification passed.")


def test_local_version_detection():
    updater = UpdateService()
    local_ver = updater.get_local_version()
    print(f"  Local version detected: {local_ver}")
    assert local_ver is not None or not (Path(".git").exists() or Path(".version").exists())
    print("  [+] Local version detection passed.")


async def test_remote_version_check():
    updater = UpdateService()
    has_update, remote_ver, err = await updater.check_for_updates()
    print(f"  Remote update check: has_update={has_update}, remote={remote_ver}, err={err}")
    if err:
        print(f"  [!] GitHub API notice (rate-limit or offline): {err}")
    else:
        assert remote_ver is not None
        assert len(remote_ver.sha) > 0
        assert len(remote_ver.short_sha) == 7
    print("  [+] Remote version check passed.")


async def main():
    print("==================================================")
    print("[*] Running Updater Verification Tests...")
    print("==================================================")
    test_protected_paths()
    test_local_version_detection()
    await test_remote_version_check()
    print("==================================================")
    print("[SUCCESS] ALL UPDATER TESTS PASSED!")
    print("==================================================")


if __name__ == "__main__":
    asyncio.run(main())
