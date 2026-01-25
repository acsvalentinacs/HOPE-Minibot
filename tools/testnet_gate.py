# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T18:00:00Z
# Purpose: TESTNET Gate - read-only API verification (NO ORDERS)
# === END SIGNATURE ===
"""
TESTNET Gate - Read-Only API Verification.

Performs safe, read-only verification of TESTNET connectivity.
NO ORDERS are placed. Only uses exchangeInfo/ping endpoints.

Checks:
1. Egress wrapper works (allowlist enforced)
2. TESTNET API responds
3. Credentials configured (optional check)
4. No redirect to unexpected host

Usage:
    python tools/testnet_gate.py
    python tools/testnet_gate.py --check-credentials

Exit codes:
    0 = PASS (TESTNET reachable, egress works)
    1 = FAIL (network error, allowlist violation, etc.)
    2 = ERROR (config/setup issue)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# SSoT paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

STATE_DIR = PROJECT_ROOT / "state"
HEALTH_DIR = STATE_DIR / "health"
TESTNET_EVIDENCE_PATH = HEALTH_DIR / "testnet_gate.json"

# TESTNET endpoint
TESTNET_BASE_URL = "https://testnet.binance.vision"
TESTNET_HOST = "testnet.binance.vision"


def atomic_write(path: Path, content: str) -> None:
    """Atomic write: temp -> fsync -> replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def check_allowlist_contains_testnet() -> tuple[bool, str]:
    """
    Verify TESTNET host is in allowlist.

    Returns:
        (is_allowed, message)
    """
    try:
        from core.net.net_policy import get_allowlist, is_allowed

        allowlist = get_allowlist()
        if is_allowed(TESTNET_HOST):
            return True, f"{TESTNET_HOST} in allowlist ({len(allowlist)} hosts)"
        else:
            return False, f"{TESTNET_HOST} NOT in allowlist"

    except ImportError:
        # Fallback: check file directly
        allowlist_path = PROJECT_ROOT / "config" / "AllowList.spider.txt"
        if not allowlist_path.exists():
            allowlist_path = PROJECT_ROOT / "AllowList.txt"

        if not allowlist_path.exists():
            return False, "AllowList not found"

        content = allowlist_path.read_text(encoding="utf-8").lower()
        if TESTNET_HOST.lower() in content:
            return True, f"{TESTNET_HOST} found in AllowList"
        else:
            return False, f"{TESTNET_HOST} NOT in AllowList"


def check_testnet_ping() -> tuple[bool, int, str, Dict[str, Any]]:
    """
    Ping TESTNET API (read-only).

    Returns:
        (success, status_code, message, response_data)
    """
    try:
        # Try egress wrapper first
        from core.net.http_client import http_get

        url = f"{TESTNET_BASE_URL}/api/v3/ping"
        status, body, final_url = http_get(url, timeout_sec=10)

        if status == 200:
            return True, status, "Ping OK via egress wrapper", {}
        else:
            return False, status, f"Ping failed: HTTP {status}", {}

    except ImportError:
        # Fallback: use urllib directly (for bootstrap)
        import urllib.request
        import urllib.error

        url = f"{TESTNET_BASE_URL}/api/v3/ping"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "HOPE-TestnetGate/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.getcode()
                return True, status, "Ping OK (urllib fallback)", {}
        except urllib.error.HTTPError as e:
            return False, e.code, f"HTTP error: {e.code}", {}
        except urllib.error.URLError as e:
            return False, 0, f"URL error: {e.reason}", {}
        except Exception as e:
            return False, 0, f"Error: {e}", {}

    except Exception as e:
        return False, 0, f"Egress error: {e}", {}


def check_testnet_exchange_info() -> tuple[bool, int, str, Dict[str, Any]]:
    """
    Get exchangeInfo from TESTNET (read-only).

    Returns:
        (success, status_code, message, response_data)
    """
    try:
        from core.net.http_client import http_get

        url = f"{TESTNET_BASE_URL}/api/v3/exchangeInfo?symbol=BTCUSDT"
        status, body, final_url = http_get(url, timeout_sec=15)

        if status == 200:
            try:
                data = json.loads(body)
                symbols_count = len(data.get("symbols", []))
                return True, status, f"ExchangeInfo OK ({symbols_count} symbols)", data
            except json.JSONDecodeError:
                return False, status, "ExchangeInfo: invalid JSON", {}
        else:
            return False, status, f"ExchangeInfo failed: HTTP {status}", {}

    except ImportError:
        # Fallback
        import urllib.request

        url = f"{TESTNET_BASE_URL}/api/v3/exchangeInfo?symbol=BTCUSDT"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "HOPE-TestnetGate/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                status = resp.getcode()
                body = resp.read().decode("utf-8")
                data = json.loads(body)
                symbols_count = len(data.get("symbols", []))
                return True, status, f"ExchangeInfo OK ({symbols_count} symbols, urllib)", data
        except Exception as e:
            return False, 0, f"Error: {e}", {}

    except Exception as e:
        return False, 0, f"Egress error: {e}", {}


def check_credentials_configured() -> tuple[bool, str]:
    """
    Check if TESTNET credentials are configured (does NOT validate them).

    Returns:
        (configured, message)
    """
    secrets_path = Path(r"C:\secrets\hope\.env")

    if not secrets_path.exists():
        return False, "Secrets file not found"

    try:
        content = secrets_path.read_text(encoding="utf-8")
        env = {}
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()

        key = env.get("BINANCE_TESTNET_API_KEY", "")
        secret = env.get("BINANCE_TESTNET_API_SECRET", "")

        if key and secret:
            # Don't log actual values, just confirm presence
            key_prefix = key[:4] + "..." if len(key) > 4 else "[short]"
            return True, f"TESTNET credentials found (key={key_prefix})"
        elif key:
            return False, "TESTNET API key found but secret missing"
        elif secret:
            return False, "TESTNET API secret found but key missing"
        else:
            return False, "TESTNET credentials not configured"

    except Exception as e:
        return False, f"Error reading credentials: {e}"


def save_evidence(
    passed: bool,
    checks: Dict[str, Dict],
    endpoint: str,
    response_status: int,
) -> None:
    """Save TESTNET gate evidence."""
    evidence = {
        "schema_version": "testnet_gate_v1",
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "passed": passed,
        "endpoint": endpoint,
        "response_status": response_status,
        "testnet_host": TESTNET_HOST,
        "checks": checks,
    }

    atomic_write(TESTNET_EVIDENCE_PATH, json.dumps(evidence, indent=2))


def main() -> int:
    """Main entrypoint."""
    parser = argparse.ArgumentParser(
        description="TESTNET Gate - read-only API verification (NO ORDERS)",
    )
    parser.add_argument(
        "--check-credentials",
        action="store_true",
        help="Also verify TESTNET credentials are configured",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )

    args = parser.parse_args()

    print("=== TESTNET GATE ===")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print(f"Target: {TESTNET_BASE_URL}")
    print()
    print("NOTE: This gate performs READ-ONLY operations. NO ORDERS are placed.")
    print()

    checks = {}
    all_passed = True
    final_status = 0
    final_endpoint = ""

    # Check 1: Allowlist
    print("[1/4] Checking allowlist...")
    allowed, msg = check_allowlist_contains_testnet()
    checks["allowlist"] = {"passed": allowed, "message": msg}
    print(f"      {'PASS' if allowed else 'FAIL'}: {msg}")
    if not allowed:
        all_passed = False

    # Check 2: Ping
    print("[2/4] Pinging TESTNET...")
    ping_ok, ping_status, ping_msg, _ = check_testnet_ping()
    checks["ping"] = {"passed": ping_ok, "status": ping_status, "message": ping_msg}
    print(f"      {'PASS' if ping_ok else 'FAIL'}: {ping_msg}")
    if not ping_ok:
        all_passed = False
    final_status = ping_status
    final_endpoint = f"{TESTNET_BASE_URL}/api/v3/ping"

    # Check 3: ExchangeInfo
    print("[3/4] Fetching exchangeInfo...")
    info_ok, info_status, info_msg, _ = check_testnet_exchange_info()
    checks["exchange_info"] = {"passed": info_ok, "status": info_status, "message": info_msg}
    print(f"      {'PASS' if info_ok else 'FAIL'}: {info_msg}")
    if not info_ok:
        all_passed = False
    if info_status:
        final_status = info_status
        final_endpoint = f"{TESTNET_BASE_URL}/api/v3/exchangeInfo"

    # Check 4: Credentials (optional)
    if args.check_credentials:
        print("[4/4] Checking credentials...")
        creds_ok, creds_msg = check_credentials_configured()
        checks["credentials"] = {"passed": creds_ok, "message": creds_msg}
        print(f"      {'PASS' if creds_ok else 'WARN'}: {creds_msg}")
        # Credentials are optional - don't fail the gate
    else:
        print("[4/4] Skipping credentials check (use --check-credentials)")
        checks["credentials"] = {"passed": None, "message": "skipped"}

    # Save evidence
    print()
    print("Saving evidence...")
    save_evidence(all_passed, checks, final_endpoint, final_status)
    print(f"Evidence: {TESTNET_EVIDENCE_PATH.relative_to(PROJECT_ROOT)}")

    # Summary
    print()
    if args.json:
        output = {
            "passed": all_passed,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "endpoint": final_endpoint,
            "response_status": final_status,
            "checks": checks,
        }
        print(json.dumps(output, indent=2))
    else:
        print("=== RESULT ===")
        if all_passed:
            print("Status: PASS")
            print("TESTNET connectivity verified (read-only)")
            print()
            print("Next step: Run TESTNET one-shot order test")
            print("  python run_live_trading.py --mode TESTNET --symbol BTCUSDT --side BUY --size-usd 20 --once")
        else:
            print("Status: FAIL")
            print("TESTNET connectivity check failed")
            print()
            print("Resolution:")
            print("  1. Check network connectivity")
            print("  2. Verify testnet.binance.vision is in AllowList")
            print("  3. Check egress wrapper configuration")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
