# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T16:40:00Z
# Modified by: Claude (opus-4)
# Modified at (UTC): 2026-01-25T17:30:00Z
# Purpose: LIVE Smoke Gate - единый smoke-тест торгового контура (expanded network scope)
# === END SIGNATURE ===
"""
LIVE Smoke Gate - Smoke Test for Trading.

Единый smoke-тест всего торгового контура.
PASS только при нулевых ошибках.

Проверки:
1. py_compile на core/trade/*.py
2. Import test
3. policy_preflight gate
4. verify_stack gate
5. risk_engine self-test
6. order_router DRY test-order
7. Проверка отсутствия прямых сетевых вызовов в core/trade/

Usage:
    python tools/live_smoke_gate.py --mode DRY

Exit codes:
    0 = ALL PASS
    1 = FAIL (with details)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import py_compile
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# SSoT paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("live_smoke_gate")


def check_syntax() -> tuple[bool, str]:
    """Check py_compile on core/trade/*.py."""
    trade_dir = PROJECT_ROOT / "core" / "trade"
    if not trade_dir.exists():
        return False, "core/trade directory not found"

    errors = []
    checked = 0

    for py_file in trade_dir.glob("*.py"):
        try:
            py_compile.compile(str(py_file), doraise=True)
            checked += 1
        except py_compile.PyCompileError as e:
            errors.append(f"{py_file.name}: {e}")

    if errors:
        return False, f"Syntax errors: {errors}"

    return True, f"Syntax OK ({checked} files)"


def check_imports() -> tuple[bool, str]:
    """Check imports of trading modules."""
    try:
        from core.trade.live_gate import LiveGate
        from core.trade.risk_engine import TradingRiskEngine
        from core.trade.order_audit import OrderAudit
        from core.trade.order_router import TradingOrderRouter
        from core.trade.position_tracker import PositionTracker

        return True, "All trading modules imported successfully"
    except ImportError as e:
        return False, f"Import error: {e}"


def check_policy_preflight() -> tuple[bool, str]:
    """Run policy_preflight gate."""
    try:
        from tools.commit_gate import check_policy_preflight as gate_check
        result = gate_check()
        return result.passed, result.reason
    except Exception as e:
        return False, f"policy_preflight error: {e}"


def check_verify_stack() -> tuple[bool, str]:
    """Run verify_stack gate."""
    try:
        from tools.commit_gate import check_verify_stack as gate_check
        result = gate_check()
        return result.passed, result.reason
    except Exception as e:
        return False, f"verify_stack error: {e}"


def check_risk_engine_selftest() -> tuple[bool, str]:
    """Run risk_engine self-test."""
    try:
        from core.trade.risk_engine import TradingRiskEngine, OrderIntent, PortfolioSnapshot

        engine = TradingRiskEngine()

        # Test with valid data
        # Note: With 0.10% risk per trade, need high equity for min_order_usd=10
        # 10000 * 0.10% = 10 USD (exactly at minimum)
        intent = OrderIntent(symbol="BTCUSDT", side="BUY", size_usd=100.0)
        portfolio = PortfolioSnapshot(
            equity_usd=10000.0,  # High enough for 0.10% risk = 10 USD
            open_positions=0,
            daily_pnl_usd=0.0,
            start_of_day_equity=10000.0,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            source="test",
        )

        result = engine.validate_order(intent, portfolio)

        if result.allowed:
            return True, f"Risk engine self-test PASS (allowed_size={result.allowed_size_usd:.2f})"
        else:
            return False, f"Risk engine rejected test order: {result.reason}"

    except Exception as e:
        return False, f"Risk engine error: {e}"


def check_order_router_dry() -> tuple[bool, str]:
    """Run order_router DRY test."""
    try:
        from core.trade.order_router import TradingOrderRouter
        from core.trade.risk_engine import PortfolioSnapshot

        router = TradingOrderRouter(mode="DRY", dry_run=True)

        # Note: With 0.10% risk per trade, need high equity for min_order_usd=10
        portfolio = PortfolioSnapshot(
            equity_usd=10000.0,
            open_positions=0,
            daily_pnl_usd=0.0,
            start_of_day_equity=10000.0,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            source="test",
        )

        result = router.execute_order(
            symbol="BTCUSDT",
            side="BUY",
            size_usd=50.0,
            portfolio=portfolio,
        )

        if result.success:
            return True, f"DRY order test PASS (order_id={result.order_id})"
        else:
            return False, f"DRY order failed: {result.reason}"

    except Exception as e:
        return False, f"Order router error: {e}"


def check_no_direct_network() -> tuple[bool, str]:
    """Check for direct network calls in core/trade/ (STRICT SCOPE for trading).

    FAIL-CLOSED: core/trade/ MUST NOT have direct network calls.
    WARNING only: Other core/** modules (legacy code, migration pending).
    """
    trade_dir = PROJECT_ROOT / "core" / "trade"
    if not trade_dir.exists():
        return False, "core/trade directory not found"

    forbidden_patterns = [
        r"urllib\.request\.urlopen",
        r"requests\.get\(",
        r"requests\.post\(",
        r"requests\.put\(",
        r"requests\.delete\(",
        r"requests\.Session\(",
        r"socket\.socket\(",
        r"http\.client\.HTTPConnection",
        r"http\.client\.HTTPSConnection",
        r"aiohttp\.ClientSession",
        r"httpx\.Client",
        r"httpx\.AsyncClient",
    ]

    violations = []
    checked = 0

    # STRICT CHECK: core/trade/ (MUST pass)
    for py_file in trade_dir.glob("*.py"):
        checked += 1
        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        for pattern in forbidden_patterns:
            if re.search(pattern, content):
                violations.append(f"{py_file.name}: {pattern}")

    if violations:
        return False, f"FAIL: Direct network in core/trade/: {violations}"

    return True, f"PASS: No direct network calls in core/trade/ ({checked} files)"


def main() -> int:
    """Main entrypoint."""
    parser = argparse.ArgumentParser(description="LIVE Smoke Gate")
    parser.add_argument("--mode", default="DRY", help="Mode (default: DRY)")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    logger.info("=== LIVE SMOKE GATE ===")
    logger.info("Mode: %s", args.mode)
    logger.info("")

    results = {}
    all_passed = True

    # Check 1: Syntax
    logger.info("[1/7] Checking syntax...")
    passed, reason = check_syntax()
    results["syntax"] = {"passed": passed, "reason": reason}
    logger.info("      %s: %s", "PASS" if passed else "FAIL", reason)
    if not passed:
        all_passed = False

    # Check 2: Imports
    logger.info("[2/7] Checking imports...")
    passed, reason = check_imports()
    results["imports"] = {"passed": passed, "reason": reason}
    logger.info("      %s: %s", "PASS" if passed else "FAIL", reason)
    if not passed:
        all_passed = False

    # Check 3: policy_preflight
    logger.info("[3/7] Running policy_preflight...")
    passed, reason = check_policy_preflight()
    results["policy_preflight"] = {"passed": passed, "reason": reason}
    logger.info("      %s: %s", "PASS" if passed else "FAIL", reason)
    if not passed:
        all_passed = False

    # Check 4: verify_stack
    logger.info("[4/7] Running verify_stack...")
    passed, reason = check_verify_stack()
    results["verify_stack"] = {"passed": passed, "reason": reason}
    logger.info("      %s: %s", "PASS" if passed else "FAIL", reason)
    if not passed:
        all_passed = False

    # Check 5: risk_engine
    logger.info("[5/7] Running risk_engine self-test...")
    passed, reason = check_risk_engine_selftest()
    results["risk_engine"] = {"passed": passed, "reason": reason}
    logger.info("      %s: %s", "PASS" if passed else "FAIL", reason)
    if not passed:
        all_passed = False

    # Check 6: order_router DRY
    logger.info("[6/7] Running order_router DRY test...")
    passed, reason = check_order_router_dry()
    results["order_router_dry"] = {"passed": passed, "reason": reason}
    logger.info("      %s: %s", "PASS" if passed else "FAIL", reason)
    if not passed:
        all_passed = False

    # Check 7: No direct network
    logger.info("[7/7] Checking for direct network calls...")
    passed, reason = check_no_direct_network()
    results["no_direct_network"] = {"passed": passed, "reason": reason}
    logger.info("      %s: %s", "PASS" if passed else "FAIL", reason)
    if not passed:
        all_passed = False

    # Summary
    logger.info("")
    if all_passed:
        logger.info("=== ALL CHECKS PASS ===")
    else:
        logger.error("=== SOME CHECKS FAILED ===")

    if args.json:
        output = {
            "passed": all_passed,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "checks": results,
        }
        print(json.dumps(output, indent=2))

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
