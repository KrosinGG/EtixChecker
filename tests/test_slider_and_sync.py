import asyncio
import sys
from pathlib import Path

# Ensure UTF-8 output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.adspower.client import AdsPowerClient
from src.adspower.profile_manager import AdsPowerProfileManager
from src.browser.human_actions import _generate_bezier_points
from src.domain.enums import ProfileRole
from src.domain.models import AdsPowerProfile
from src.storage.proxy_sync import ProxySyncService


def test_proxy_parser():
    print("[1/4] Testing proxy string parser...")
    cfg1 = AdsPowerClient.parse_proxy_string("1.2.3.4:8080:user:pass")
    assert cfg1["proxy_host"] == "1.2.3.4"
    assert cfg1["proxy_port"] == "8080"
    assert cfg1["proxy_user"] == "user"
    assert cfg1["proxy_password"] == "pass"

    cfg2 = AdsPowerClient.parse_proxy_string("socks5://user:pass@5.6.7.8:1080")
    assert cfg2["proxy_type"] == "socks5"
    assert cfg2["proxy_host"] == "5.6.7.8"
    assert cfg2["proxy_port"] == "1080"
    print("  [+] Proxy parser verified.")


def test_bezier_trajectory():
    print("[2/4] Testing Bezier curve generation for slider...")
    pts = _generate_bezier_points(100, 200, 380, 200, steps=20)
    assert len(pts) == 20
    assert pts[0][0] > 100
    assert abs(pts[-1][0] - 380) < 1.0
    print("  [+] Bezier trajectory generator verified.")


def test_proxy_sync_service():
    print("[3/4] Testing ProxySyncService local operations...")
    tmp_file = Path("data/scratch_good_proxies.txt")
    service = ProxySyncService(local_file=tmp_file)
    service.save_local_proxies({"1.1.1.1:8080:u:p", "2.2.2.2:8080:u:p"})
    loaded = service.load_local_proxies()
    assert "1.1.1.1:8080:u:p" in loaded
    assert len(loaded) == 2
    if tmp_file.exists():
        tmp_file.unlink()
    print("  [+] ProxySyncService verified.")


def test_session_bad_proxy():
    print("[4/4] Testing session-scoped bad proxy tracking...")
    client = AdsPowerClient()
    mgr = AdsPowerProfileManager(client=client)
    p_key = "142.173.32.118:13533"
    assert not mgr.is_proxy_bad_in_session(p_key)
    mgr.record_bad_proxy(p_key, "Test session block")
    assert mgr.is_proxy_bad_in_session(p_key)
    print("  [+] Session bad proxy tracking verified.")


if __name__ == "__main__":
    test_proxy_parser()
    test_bezier_trajectory()
    test_proxy_sync_service()
    test_session_bad_proxy()
    print("\n[SUCCESS] ALL RECOVERY AND SYNC TESTS PASSED!")
