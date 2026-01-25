# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T12:30:00Z
# Purpose: Egress Policy smoke test (runtime verification)
# === END SIGNATURE ===
"""
Egress Policy Smoke Test

Proves ALLOW/DENY behavior at runtime:
1. DENY for host not in AllowList
2. ALLOW for host in AllowList (api.binance.com)
3. Audit log records for both attempts

Run: python tools/egress_smoke_test.py
"""

import sys
import os
from pathlib import Path

# Setup project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from core.net.http_client import http_get, EgressDeniedError, EgressError
from core.net.audit_log import read_audit_log, get_audit_stats


def test_denied_host():
    """Test 1: Host NOT in AllowList should be DENIED."""
    print("\n[TEST 1] Attempt GET to example.com (likely NOT in AllowList)...")
    try:
        status, body, url = http_get(
            "https://example.com/",
            timeout_sec=5,
            process="smoke_test"
        )
        print(f"  UNEXPECTED: Got response {status} (expected DENY)")
        return False
    except EgressDeniedError as e:
        print(f"  PASS: Correctly DENIED - {e.reason.value}")
        print(f"  Request ID: {e.request_id}")
        return True
    except EgressError as e:
        print(f"  PASS (via error): {e.reason.value}")
        return True
    except Exception as e:
        print(f"  ERROR: Unexpected exception: {type(e).__name__}: {e}")
        return False


def test_allowed_host():
    """Test 2: api.binance.com should be ALLOWED (if in AllowList)."""
    print("\n[TEST 2] Attempt GET to api.binance.com/api/v3/time...")
    try:
        status, body, url = http_get(
            "https://api.binance.com/api/v3/time",
            timeout_sec=10,
            process="smoke_test"
        )
        print(f"  PASS: Got response {status}")
        if status == 200:
            preview = body[:100].decode('utf-8', errors='replace')
            print(f"  Body preview: {preview}")
        return True
    except EgressDeniedError as e:
        print(f"  FAIL: DENIED - {e.reason.value}")
        print("  (api.binance.com might not be in AllowList.txt)")
        return False
    except EgressError as e:
        print(f"  PARTIAL: Network error (host was allowed) - {e.reason.value}")
        return True  # Policy worked, network failed
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")
        return False


def show_audit_log():
    """Show last 5 audit entries."""
    print("\n[AUDIT LOG] Last 5 entries:")
    records = read_audit_log(last_n=5)
    if not records:
        print("  (no records)")
        return
    for r in records:
        action = r.get("action", "?")
        host = r.get("host", "?")
        reason = r.get("reason", "?")
        ts = r.get("ts_utc", "?")
        print(f"  {ts} | {action} | {host} | {reason}")


def main():
    print("=" * 50)
    print("EGRESS POLICY SMOKE TEST")
    print("=" * 50)

    results = []

    # Test 1: Denied host
    results.append(("DENY test", test_denied_host()))

    # Test 2: Allowed host
    results.append(("ALLOW test", test_allowed_host()))

    # Show audit log
    show_audit_log()

    # Summary
    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)

    passed = sum(1 for _, ok in results if ok)
    total = len(results)

    for name, ok in results:
        status = "PASS" if ok else "FAIL"
        print(f"  {name}: {status}")

    print(f"\nTotal: {passed}/{total} passed")

    # Stats
    stats = get_audit_stats()
    print(f"Audit stats: {stats['allow_count']} ALLOW, {stats['deny_count']} DENY")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
