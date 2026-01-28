# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T12:40:00Z
# Purpose: Test TradingOrderRouter in DRY mode
# === END SIGNATURE ===
"""Quick DRY mode test for TradingOrderRouter."""
from __future__ import annotations

import io
import sys
import os

# UTF-8 for Windows
if sys.stdout and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.trade.order_router import TradingOrderRouter, ExecutionStatus

def main():
    print("=" * 50)
    print("TradingOrderRouter DRY MODE TEST")
    print("=" * 50)

    # Initialize in DRY mode (no RiskGovernor needed)
    router = TradingOrderRouter(mode="DRY", dry_run=True)

    status = router.get_status()
    print(f"Router Status:")
    print(f"  Mode: {status['mode']}")
    print(f"  Dry Run: {status['dry_run']}")
    print(f"  Session: {status['session_id']}")
    print()

    # Execute test order (0.00012 BTC * 90000 ~= $10.8 > min $10)
    print("Executing test order: BUY 0.00012 BTCUSDT @ MARKET (~$10.80)")
    result = router.execute_order(
        symbol="BTCUSDT",
        side="BUY",
        quantity=0.00012,  # ~$10.80 at $90,000 price
        order_type="MARKET",
        signal_id="test_signal_001"
    )

    print()
    print("-" * 50)
    print(f"Result Status: {result.status.value}")
    print(f"Client Order ID: {result.client_order_id[:32]}...")
    print(f"Symbol: {result.symbol}")
    print(f"Side: {result.side}")
    print(f"Quantity: {result.quantity}")
    print(f"Price: {result.price}")
    print(f"Notional: {result.notional}")
    print(f"Message: {result.message}")
    print()

    if result.status == ExecutionStatus.SUCCESS:
        print("RESULT: PASS - DRY order execution successful")
        return 0
    else:
        print(f"RESULT: FAIL - {result.message}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
