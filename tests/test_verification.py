"""Comprehensive verification test for Etix Checker with AdsPower integration."""

import asyncio
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Add root directory to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.settings import CONFIG
from src.domain.enums import ShowStatus, ProfileRole
from src.domain.models import Show, CheckResult, AdsPowerProfile
from src.adspower.client import AdsPowerClient
from src.adspower.profile_manager import AdsPowerProfileManager
from src.adspower.backup_service import ProfileBackupService
from src.etix.detector import EtixDetector
from src.etix.cart_handler import EtixCartHandler
from src.etix.checker import EtixCheckEngine
from src.storage.checkpoint import RunContext, compute_csv_fingerprint
from src.storage.reporter import Reporter


async def run_all_tests():
    print("==================================================")
    print("[*] Running Etix Checker Verification Tests...")
    print("==================================================")

    # Test 1: Config and Domain Models
    print("[1/6] Testing domain models and config...")
    show = Show(show_id="test_1", name="Test Event", url="https://etix.com/test", target_total=4, max_per_order=2)
    assert show.show_id == "test_1"
    assert show.target_total == 4
    result = CheckResult(
        show_id="test_1",
        name="Test Event",
        url="https://etix.com/test",
        status=ShowStatus.OK,
        target=4,
        reserved=4,
        available_approx="4",
        details="Success",
    )
    row = result.to_report_row()
    assert "url" not in row
    assert row["status"] == "OK"
    print("  [+] Domain models verified.")

    # Test 2: Shows loader
    print("[2/6] Testing shows loader...")
    engine = EtixCheckEngine(config=CONFIG)
    shows = engine.load_shows(CONFIG.shows_csv)
    print(f"  Loaded {len(shows)} shows from {CONFIG.shows_csv}")
    assert len(shows) > 0, "No shows loaded from shows.csv"
    print("  [+] Shows loader verified.")

    # Test 3: Checkpoint & RunContext
    print("[3/6] Testing RunContext and Checkpoint...")
    fp = compute_csv_fingerprint(shows)
    assert len(fp) > 0
    ctx = RunContext(shows=shows, run_id="test_run")
    show_id = shows[0].show_id
    test_res = CheckResult(
        show_id=show_id,
        name=shows[0].name,
        url=shows[0].url,
        status=ShowStatus.OK,
        target=shows[0].target_total,
        reserved=shows[0].target_total,
        details="Done",
    )
    ctx.mark_inflight(show_id)
    ctx.commit_done(test_res)
    assert show_id in ctx.done_results
    ctx.complete_run()
    print("  [+] RunContext & Checkpoint verified.")

    # Test 4: AdsPower Local API Client
    print("[4/6] Testing AdsPower Local API connectivity...")
    client = AdsPowerClient(base_url=CONFIG.adspower_api_url)
    status_ok = await client.check_status()
    print(f"  AdsPower status check: {'ONLINE' if status_ok else 'OFFLINE'}")
    assert status_ok, "AdsPower Local API is not reachable on port 50325!"

    groups = await client.get_groups()
    print(f"  Found {len(groups)} groups in AdsPower.")
    group_id = await client.find_group_id_by_name(CONFIG.adspower_group_name, cached_groups=groups)
    print(f"  Target group '{CONFIG.adspower_group_name}' ID: {group_id}")
    assert group_id is not None, f"Group '{CONFIG.adspower_group_name}' not found!"
    print("  [+] AdsPower Client verified.")

    # Test 5: Profile Manager and Backup Service
    print("[5/6] Testing AdsPower Profile Manager and Backup Service...")
    backup_service = ProfileBackupService(backup_dir=Path("data/adspower_backup"))
    manager = AdsPowerProfileManager(client=client, backup_service=backup_service)
    profiles = await manager.load_and_organize_profiles(group_name=CONFIG.adspower_group_name, active_count=12)
    print(f"  Loaded {len(profiles)} profiles for '{CONFIG.adspower_group_name}'")
    assert len(profiles) >= 12, f"Expected at least 12 profiles, got {len(profiles)}"
    active = manager.get_active_profiles()
    reserve = manager.get_reserve_profiles()
    print(f"  Active profiles: {len(active)}, Reserve profiles: {len(reserve)}")
    assert len(active) == 12, f"Expected 12 active profiles, got {len(active)}"

    # Check backup file
    latest_backup = backup_service.load_latest_backup()
    assert latest_backup is not None
    assert latest_backup.get("total_profiles") == len(profiles)
    print("  [+] Profile Manager & Backup Service verified.")

    # Test 6: Reporter
    print("[6/7] Testing Reporter...")
    reporter = Reporter()
    test_report_path = reporter.save_report([result])
    assert test_report_path.exists()
    print("  [+] Reporter verified.")

    # Test 7: UpdateService
    print("[7/9] Testing UpdateService...")
    from src.utils.updater import UpdateService
    updater = UpdateService()
    assert updater._is_path_protected(".env")
    assert updater._is_path_protected("data/shows.csv")
    assert updater._is_path_protected("src/etix/checker.py") is False
    local_ver = updater.get_local_version()
    print(f"  Detected local version: {local_ver.short_sha if local_ver else 'None'}")
    print("  [+] UpdateService verified.")

    # Test 8: Bezier Curve & Human Motion Physics
    print("[8/9] Testing Bezier curve & human motion kinematics...")
    from src.browser.human_actions import _generate_bezier_points
    start_x, start_y = 100.0, 200.0
    end_x, end_y = 380.0, 200.0
    points = _generate_bezier_points(start_x, start_y, end_x, end_y, steps=30)
    assert len(points) == 30, f"Expected 30 points, got {len(points)}"
    first_pt = points[0]
    last_pt = points[-1]
    assert first_pt[0] > start_x, "First step should advance along X"
    assert abs(last_pt[0] - end_x) < 1.0, f"Last point X {last_pt[0]} should reach end_x {end_x}"
    # Verify Y tremor jitter exists in middle trajectory
    y_jitters = [abs(p[1] - start_y) for p in points[5:25]]
    assert any(j > 0.05 for j in y_jitters), "Human Y micro-jitter not detected in middle trajectory"
    print("  [+] Bezier kinematics & micro-jitter verified.")

    # Test 9: Selective Shows Filtering in CheckEngine
    print("[9/9] Testing selective shows filtering...")
    assert len(shows) >= 1, "Need at least 1 show to test selection"
    selected_subset = [shows[0]]
    sub_ctx = RunContext(shows=selected_subset, run_id="subset_test")
    assert len(sub_ctx.pending_ids) == 1
    assert sub_ctx.pending_ids[0] == shows[0].show_id
    sub_ctx.complete_run()
    print("  [+] Selective shows filtering verified.")

    print("==================================================")
    print("[SUCCESS] ALL TESTS PASSED!")
    print("==================================================")


if __name__ == "__main__":
    asyncio.run(run_all_tests())
