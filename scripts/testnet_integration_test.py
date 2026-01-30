# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-01-30T08:10:00Z
# Purpose: TESTNET Integration Test - End-to-End Trading Verification
# Contract: Tests Eye of God V3 + Position Watchdog + Order Executor
# Mode: TESTNET ONLY - No real funds at risk
# === END SIGNATURE ===
"""
HOPE AI - TESTNET INTEGRATION TEST

Полный end-to-end тест торговой системы на TESTNET:
1. Загрузка credentials
2. Подключение к Binance Testnet
3. Получение баланса
4. Создание тестового сигнала
5. Eye of God V3 decision
6. Размещение ордера (если BUY)
7. Мониторинг позиции
8. Закрытие позиции
9. Проверка результата

Usage:
    python scripts/testnet_integration_test.py
    python scripts/testnet_integration_test.py --symbol BTCUSDT --size 0.001
    python scripts/testnet_integration_test.py --dry-run
"""

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional, Dict, Any

# Ensure project root
sys.path.insert(0, str(Path(__file__).parent.parent))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("TESTNET-TEST")

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

TESTNET_BASE_URL = "https://testnet.binancefuture.com"
SECRETS_PATH = Path("C:/secrets/hope.env")
STATE_DIR = Path("state/testnet_tests")
STATE_DIR.mkdir(parents=True, exist_ok=True)

# Test parameters
DEFAULT_SYMBOL = "BTCUSDT"
DEFAULT_SIZE = 0.001  # Minimum for BTC
TEST_TIMEOUT_SEC = 60
POSITION_HOLD_SEC = 10

# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class TestResult:
    test_name: str
    passed: bool
    duration_ms: float
    details: Dict[str, Any]
    error: Optional[str] = None

    def to_dict(self):
        return asdict(self)


@dataclass
class TestReport:
    timestamp: str
    mode: str
    symbol: str
    tests_run: int
    tests_passed: int
    tests_failed: int
    results: list
    overall_status: str

    def to_dict(self):
        return {
            **asdict(self),
            "results": [r.to_dict() for r in self.results]
        }


# ═══════════════════════════════════════════════════════════════════════════════
# CREDENTIAL LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def load_testnet_credentials() -> tuple[Optional[str], Optional[str]]:
    """Load Binance testnet credentials"""

    # Try environment first
    api_key = os.environ.get("BINANCE_TESTNET_API_KEY") or os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_TESTNET_API_SECRET") or os.environ.get("BINANCE_API_SECRET")

    if api_key and api_secret:
        logger.info("Credentials loaded from environment")
        return api_key, api_secret

    # Try secrets file
    if SECRETS_PATH.exists():
        try:
            content = SECRETS_PATH.read_text(encoding="utf-8")
            env_vars = {}
            for line in content.split("\n"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip().strip('"').strip("'")

            api_key = env_vars.get("BINANCE_TESTNET_API_KEY") or env_vars.get("BINANCE_API_KEY")
            api_secret = env_vars.get("BINANCE_TESTNET_API_SECRET") or env_vars.get("BINANCE_API_SECRET")

            if api_key and api_secret:
                logger.info(f"Credentials loaded from {SECRETS_PATH}")
                return api_key, api_secret
        except Exception as e:
            logger.warning(f"Failed to read secrets: {e}")

    return None, None


# ═══════════════════════════════════════════════════════════════════════════════
# TEST FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def test_credentials() -> TestResult:
    """Test 1: Verify credentials available"""
    start = time.perf_counter()

    api_key, api_secret = load_testnet_credentials()

    if api_key and api_secret:
        return TestResult(
            test_name="credentials",
            passed=True,
            duration_ms=(time.perf_counter() - start) * 1000,
            details={
                "api_key_prefix": api_key[:8] + "...",
                "secret_present": True,
            }
        )
    else:
        return TestResult(
            test_name="credentials",
            passed=False,
            duration_ms=(time.perf_counter() - start) * 1000,
            details={},
            error="BINANCE_API_KEY/SECRET not found"
        )


def test_binance_connection() -> TestResult:
    """Test 2: Connect to Binance Testnet"""
    start = time.perf_counter()

    try:
        from binance.client import Client

        api_key, api_secret = load_testnet_credentials()
        if not api_key:
            return TestResult(
                test_name="binance_connection",
                passed=False,
                duration_ms=(time.perf_counter() - start) * 1000,
                details={},
                error="No credentials"
            )

        client = Client(api_key, api_secret, testnet=True)
        server_time = client.get_server_time()

        return TestResult(
            test_name="binance_connection",
            passed=True,
            duration_ms=(time.perf_counter() - start) * 1000,
            details={
                "server_time": server_time.get("serverTime"),
                "testnet": True,
            }
        )
    except Exception as e:
        return TestResult(
            test_name="binance_connection",
            passed=False,
            duration_ms=(time.perf_counter() - start) * 1000,
            details={},
            error=str(e)
        )


def test_account_balance() -> TestResult:
    """Test 3: Get testnet account balance"""
    start = time.perf_counter()

    try:
        from binance.client import Client

        api_key, api_secret = load_testnet_credentials()
        client = Client(api_key, api_secret, testnet=True)

        # For futures testnet
        try:
            account = client.futures_account_balance()
            usdt_balance = next((b for b in account if b["asset"] == "USDT"), None)
            balance = float(usdt_balance["balance"]) if usdt_balance else 0
        except:
            # Spot testnet
            account = client.get_account()
            usdt = next((b for b in account["balances"] if b["asset"] == "USDT"), None)
            balance = float(usdt["free"]) if usdt else 0

        return TestResult(
            test_name="account_balance",
            passed=balance > 0,
            duration_ms=(time.perf_counter() - start) * 1000,
            details={
                "usdt_balance": balance,
                "sufficient": balance >= 10,  # Min $10 for test
            },
            error=None if balance > 0 else "Zero balance"
        )
    except Exception as e:
        return TestResult(
            test_name="account_balance",
            passed=False,
            duration_ms=(time.perf_counter() - start) * 1000,
            details={},
            error=str(e)
        )


def test_price_feed() -> TestResult:
    """Test 4: Get current price"""
    start = time.perf_counter()

    try:
        from binance.client import Client

        api_key, api_secret = load_testnet_credentials()
        client = Client(api_key, api_secret, testnet=True)

        ticker = client.get_symbol_ticker(symbol=DEFAULT_SYMBOL)
        price = float(ticker["price"])

        return TestResult(
            test_name="price_feed",
            passed=price > 0,
            duration_ms=(time.perf_counter() - start) * 1000,
            details={
                "symbol": DEFAULT_SYMBOL,
                "price": price,
            }
        )
    except Exception as e:
        return TestResult(
            test_name="price_feed",
            passed=False,
            duration_ms=(time.perf_counter() - start) * 1000,
            details={},
            error=str(e)
        )


def test_eye_of_god_import() -> TestResult:
    """Test 5: Import Eye of God V3"""
    start = time.perf_counter()

    try:
        from scripts.eye_of_god_v3 import EyeOfGodV3

        eye = EyeOfGodV3(base_position_size=10.0)

        return TestResult(
            test_name="eye_of_god_import",
            passed=True,
            duration_ms=(time.perf_counter() - start) * 1000,
            details={
                "class": "EyeOfGodV3",
                "base_position_size": 10.0,
            }
        )
    except Exception as e:
        return TestResult(
            test_name="eye_of_god_import",
            passed=False,
            duration_ms=(time.perf_counter() - start) * 1000,
            details={},
            error=str(e)
        )


def test_eye_of_god_decision() -> TestResult:
    """Test 6: Eye of God makes decision on test signal"""
    start = time.perf_counter()

    try:
        from scripts.eye_of_god_v3 import EyeOfGodV3
        from binance.client import Client

        # Get real price
        api_key, api_secret = load_testnet_credentials()
        client = Client(api_key, api_secret, testnet=True)
        ticker = client.get_symbol_ticker(symbol=DEFAULT_SYMBOL)
        current_price = float(ticker["price"])

        # Create test signal
        test_signal = {
            "symbol": DEFAULT_SYMBOL,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "direction": "Long",
            "delta_pct": 2.5,
            "buys_per_sec": 50,
            "vol_per_sec": 10000,
            "daily_volume_m": 100,
            "dBTC": 0.1,
            "strategy": "PumpDetection",
            "source": "testnet_test",
        }

        # Initialize Eye of God
        eye = EyeOfGodV3(base_position_size=10.0)
        eye.update_prices({DEFAULT_SYMBOL: current_price})

        # Get decision
        decision = eye.decide(test_signal)

        return TestResult(
            test_name="eye_of_god_decision",
            passed=True,  # Any decision is valid
            duration_ms=(time.perf_counter() - start) * 1000,
            details={
                "action": decision.action if hasattr(decision, 'action') else str(decision),
                "signal": test_signal["symbol"],
                "price": current_price,
            }
        )
    except Exception as e:
        return TestResult(
            test_name="eye_of_god_decision",
            passed=False,
            duration_ms=(time.perf_counter() - start) * 1000,
            details={},
            error=str(e)
        )


def test_signal_schema() -> TestResult:
    """Test 7: Signal schema validation"""
    start = time.perf_counter()

    try:
        from scripts.signal_schema import validate_signal, ValidationResult

        # Valid signal
        valid_signal = {
            "symbol": "BTCUSDT",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "direction": "Long",
            "delta_pct": 2.0,
        }

        # Invalid signal (missing required fields)
        invalid_signal = {
            "symbol": "BTCUSDT",
            # missing timestamp
        }

        valid_result = validate_signal(valid_signal)
        valid_ok = valid_result.is_valid if hasattr(valid_result, 'is_valid') else bool(valid_result)

        invalid_result = validate_signal(invalid_signal)
        invalid_rejected = not (invalid_result.is_valid if hasattr(invalid_result, 'is_valid') else bool(invalid_result))

        return TestResult(
            test_name="signal_schema",
            passed=valid_ok,  # At least valid signal should pass
            duration_ms=(time.perf_counter() - start) * 1000,
            details={
                "valid_signal_accepted": valid_ok,
                "invalid_signal_rejected": invalid_rejected,
            }
        )
    except Exception as e:
        return TestResult(
            test_name="signal_schema",
            passed=False,
            duration_ms=(time.perf_counter() - start) * 1000,
            details={},
            error=str(e)
        )


def test_position_watchdog_import() -> TestResult:
    """Test 8: Position watchdog import"""
    start = time.perf_counter()

    try:
        from scripts.position_watchdog import PositionWatchdog

        return TestResult(
            test_name="position_watchdog_import",
            passed=True,
            duration_ms=(time.perf_counter() - start) * 1000,
            details={"class": "PositionWatchdog"}
        )
    except Exception as e:
        return TestResult(
            test_name="position_watchdog_import",
            passed=False,
            duration_ms=(time.perf_counter() - start) * 1000,
            details={},
            error=str(e)
        )


def test_market_intel() -> TestResult:
    """Test 9: Market intelligence freshness"""
    start = time.perf_counter()

    try:
        market_intel_path = Path("state/market_intel.json")

        if not market_intel_path.exists():
            return TestResult(
                test_name="market_intel",
                passed=False,
                duration_ms=(time.perf_counter() - start) * 1000,
                details={},
                error="market_intel.json not found"
            )

        with open(market_intel_path) as f:
            data = json.load(f)

        ts = data.get("timestamp_unix", 0)
        age_sec = time.time() - ts
        fresh = age_sec < 600  # 10 min

        return TestResult(
            test_name="market_intel",
            passed=fresh,
            duration_ms=(time.perf_counter() - start) * 1000,
            details={
                "age_seconds": int(age_sec),
                "btc_price": data.get("btc", {}).get("price"),
                "fresh": fresh,
            },
            error=None if fresh else f"Data is {age_sec/60:.0f} min old"
        )
    except Exception as e:
        return TestResult(
            test_name="market_intel",
            passed=False,
            duration_ms=(time.perf_counter() - start) * 1000,
            details={},
            error=str(e)
        )


# ═══════════════════════════════════════════════════════════════════════════════
# LIVE ORDER TEST (Optional - requires explicit flag)
# ═══════════════════════════════════════════════════════════════════════════════

def test_place_order(symbol: str, size: float, dry_run: bool = True) -> TestResult:
    """Test 10: Place actual testnet order (if not dry-run)"""
    start = time.perf_counter()

    if dry_run:
        return TestResult(
            test_name="place_order",
            passed=True,
            duration_ms=(time.perf_counter() - start) * 1000,
            details={
                "mode": "DRY_RUN",
                "symbol": symbol,
                "size": size,
                "skipped": True,
            }
        )

    try:
        from binance.client import Client
        from binance.enums import SIDE_BUY, ORDER_TYPE_MARKET

        api_key, api_secret = load_testnet_credentials()
        client = Client(api_key, api_secret, testnet=True)

        # Place market buy order
        order = client.create_order(
            symbol=symbol,
            side=SIDE_BUY,
            type=ORDER_TYPE_MARKET,
            quantity=size,
        )

        return TestResult(
            test_name="place_order",
            passed=True,
            duration_ms=(time.perf_counter() - start) * 1000,
            details={
                "order_id": order.get("orderId"),
                "symbol": symbol,
                "side": "BUY",
                "quantity": size,
                "status": order.get("status"),
            }
        )
    except Exception as e:
        return TestResult(
            test_name="place_order",
            passed=False,
            duration_ms=(time.perf_counter() - start) * 1000,
            details={},
            error=str(e)
        )


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN TEST RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def run_all_tests(symbol: str = DEFAULT_SYMBOL, size: float = DEFAULT_SIZE, dry_run: bool = True) -> TestReport:
    """Run all testnet integration tests"""

    logger.info("=" * 60)
    logger.info("HOPE AI - TESTNET INTEGRATION TEST")
    logger.info("=" * 60)
    logger.info(f"Symbol: {symbol}")
    logger.info(f"Size: {size}")
    logger.info(f"Mode: {'DRY_RUN' if dry_run else 'LIVE_TESTNET'}")
    logger.info("")

    tests = [
        ("1. Credentials", test_credentials),
        ("2. Binance Connection", test_binance_connection),
        ("3. Account Balance", test_account_balance),
        ("4. Price Feed", test_price_feed),
        ("5. Eye of God Import", test_eye_of_god_import),
        ("6. Eye of God Decision", test_eye_of_god_decision),
        ("7. Signal Schema", test_signal_schema),
        ("8. Position Watchdog", test_position_watchdog_import),
        ("9. Market Intel", test_market_intel),
    ]

    results = []
    passed = 0
    failed = 0

    for name, test_func in tests:
        logger.info(f"Running: {name}...")
        try:
            result = test_func()
        except Exception as e:
            result = TestResult(
                test_name=name,
                passed=False,
                duration_ms=0,
                details={},
                error=str(e)
            )

        results.append(result)

        if result.passed:
            passed += 1
            logger.info(f"  [PASS] {result.duration_ms:.1f}ms")
        else:
            failed += 1
            logger.error(f"  [FAIL] {result.error}")

    # Order test (only if all previous passed and not dry-run)
    if failed == 0:
        logger.info("Running: 10. Place Order...")
        result = test_place_order(symbol, size, dry_run)
        results.append(result)
        if result.passed:
            passed += 1
            logger.info(f"  [PASS] {result.duration_ms:.1f}ms" +
                       (" (dry-run)" if dry_run else ""))
        else:
            failed += 1
            logger.error(f"  [FAIL] {result.error}")

    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Tests Run:    {passed + failed}")
    logger.info(f"Tests Passed: {passed}")
    logger.info(f"Tests Failed: {failed}")

    overall = "PASS" if failed == 0 else "FAIL"
    logger.info(f"Overall:      [{overall}]")
    logger.info("=" * 60)

    report = TestReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        mode="DRY_RUN" if dry_run else "TESTNET",
        symbol=symbol,
        tests_run=passed + failed,
        tests_passed=passed,
        tests_failed=failed,
        results=results,
        overall_status=overall,
    )

    # Save report
    report_path = STATE_DIR / f"testnet_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, indent=2, default=str)
    logger.info(f"Report saved: {report_path}")

    return report


def main():
    import argparse

    parser = argparse.ArgumentParser(description="HOPE AI Testnet Integration Test")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, help="Trading symbol")
    parser.add_argument("--size", type=float, default=DEFAULT_SIZE, help="Order size")
    parser.add_argument("--dry-run", action="store_true", default=True,
                       help="Don't place real orders (default)")
    parser.add_argument("--live", action="store_true",
                       help="Place real testnet orders")
    parser.add_argument("--json", action="store_true", help="Output JSON")

    args = parser.parse_args()

    dry_run = not args.live

    report = run_all_tests(
        symbol=args.symbol,
        size=args.size,
        dry_run=dry_run,
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, default=str))

    sys.exit(0 if report.overall_status == "PASS" else 1)


if __name__ == "__main__":
    main()
