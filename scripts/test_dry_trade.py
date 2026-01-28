# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T12:35:00Z
# Purpose: Test MicroTradeExecutor in DRY mode
# === END SIGNATURE ===
"""Quick DRY mode test for MicroTradeExecutor."""
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

from core.trade.micro_trade_executor import MicroTradeExecutor, TradeConfig, TradeMode

def main():
    print("=" * 50)
    print("MicroTradeExecutor DRY MODE TEST")
    print("=" * 50)

    cfg = TradeConfig(
        symbol="BTCUSDT",
        quote_amount=10.0,
        tp_percent=0.5,
        timeout_minutes=1,  # Short timeout for test
        mode=TradeMode.DRY,
    )

    print(f"Config: {cfg.symbol} ${cfg.quote_amount} TP={cfg.tp_percent}%")
    print(f"Mode: {cfg.mode.value}")
    print(f"Job ID: {cfg.job_id}")
    print()

    executor = MicroTradeExecutor(cfg)

    print("Running state machine...")
    result = executor.run()

    print()
    print("-" * 50)
    print(f"Final State: {result.state}")
    print(f"Outcome: {result.outcome}")
    print(f"PnL: {result.pnl_usdt}")
    print()

    if result.buy_price:
        print(f"Buy Price: {result.buy_price}")
    if result.tp_price:
        print(f"TP Price: {result.tp_price}")
    if result.exit_price:
        print(f"Exit Price: {result.exit_price}")

    print()
    print("Transitions:")
    for t in result.transitions:
        print(f"  {t['from']} -> {t['to']} ({t['reason']})")

    print()
    if result.outcome in ("profit", "timeout_exit"):
        print("RESULT: PASS - DRY mode execution successful")
        return 0
    else:
        print(f"RESULT: FAIL - {result.error}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
