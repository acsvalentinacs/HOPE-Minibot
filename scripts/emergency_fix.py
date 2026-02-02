# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-02-02 16:00:00 UTC
# Purpose: Emergency position fix - sync watchdog with Binance and close stuck positions
# === END SIGNATURE ===
"""
EMERGENCY FIX - Watchdog Sync & Position Close

Проблема:
- Watchdog рассинхронизирован с Binance
- Ghost positions (SENT, ENSO) - уже закрыты на Binance
- ARDR открыта но не в watchdog, stop loss не сработал

Действия:
1. Закрыть ARDR (убыток -5.5%)
2. Очистить watchdog от ghost positions
3. Синхронизировать с Binance

Запуск:
    python scripts/emergency_fix.py --execute
    python scripts/emergency_fix.py --dry-run  (по умолчанию)
"""

import os
import sys
import json
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Set

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from binance.client import Client
from dotenv import load_dotenv

# Load secrets
load_dotenv('C:/secrets/hope.env')

# Files
WATCHDOG_POSITIONS = PROJECT_ROOT / "state/ai/watchdog/positions.json"
WATCHDOG_CLOSES = PROJECT_ROOT / "state/ai/watchdog/closes.jsonl"
EMERGENCY_LOG = PROJECT_ROOT / "state/emergency_actions.jsonl"

# Assets to ignore (not trading positions)
IGNORE_ASSETS = {'USDT', 'USDC', 'BNB', 'AUD', 'SLF', 'FDUSD', 'BUSD', 'EUR', 'GBP', 'RUB'}


def get_binance_client() -> Client:
    """Get authenticated Binance client."""
    return Client(
        os.getenv('BINANCE_API_KEY'),
        os.getenv('BINANCE_API_SECRET')
    )


def get_real_positions(client: Client) -> Dict[str, Dict]:
    """Get real positions from Binance."""
    account = client.get_account()
    positions = {}

    for b in account['balances']:
        asset = b['asset']
        free = float(b['free'])
        locked = float(b['locked'])
        total = free + locked

        if asset in IGNORE_ASSETS or total < 0.001:
            continue

        symbol = f"{asset}USDT"
        try:
            ticker = client.get_symbol_ticker(symbol=symbol)
            price = float(ticker['price'])
            value = total * price

            positions[symbol] = {
                "asset": asset,
                "quantity": total,
                "price": price,
                "value_usdt": value,
            }
        except Exception as e:
            print(f"  Warning: Could not get price for {symbol}: {e}")

    return positions


def get_watchdog_positions() -> List[Dict]:
    """Get positions from watchdog state file."""
    if not WATCHDOG_POSITIONS.exists():
        return []

    try:
        data = json.loads(WATCHDOG_POSITIONS.read_text())
        return data.get("positions", [])
    except Exception as e:
        print(f"  Warning: Could not read watchdog positions: {e}")
        return []


def close_position(client: Client, symbol: str, quantity: float, dry_run: bool = True) -> Dict:
    """Close position by selling."""
    result = {
        "symbol": symbol,
        "quantity": quantity,
        "action": "SELL",
        "status": "DRY_RUN" if dry_run else "PENDING",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if dry_run:
        print(f"  [DRY-RUN] Would sell {quantity} {symbol}")
        return result

    try:
        # Get symbol info for precision
        info = client.get_symbol_info(symbol)
        step_size = None
        for f in info['filters']:
            if f['filterType'] == 'LOT_SIZE':
                step_size = float(f['stepSize'])
                break

        # Round quantity to valid precision
        if step_size:
            precision = len(str(step_size).rstrip('0').split('.')[-1])
            quantity = round(quantity, precision)

        # Execute market sell
        order = client.create_order(
            symbol=symbol,
            side='SELL',
            type='MARKET',
            quantity=quantity
        )

        result["status"] = "SUCCESS"
        result["order_id"] = order.get("orderId")
        result["filled_qty"] = float(order.get("executedQty", 0))
        result["avg_price"] = sum(float(f['price']) * float(f['qty']) for f in order.get('fills', [])) / max(float(order.get("executedQty", 1)), 0.0001)

        print(f"  [EXECUTED] Sold {result['filled_qty']} {symbol} @ ${result['avg_price']:.6f}")

    except Exception as e:
        result["status"] = "FAILED"
        result["error"] = str(e)
        print(f"  [FAILED] Could not sell {symbol}: {e}")

    return result


def clean_watchdog(real_positions: Dict[str, Dict], dry_run: bool = True):
    """Clean watchdog positions file - remove ghost positions."""
    watchdog_positions = get_watchdog_positions()

    if not watchdog_positions:
        print("  Watchdog has no positions")
        return

    real_symbols = set(real_positions.keys())
    cleaned_positions = []
    removed = []

    for pos in watchdog_positions:
        symbol = pos.get("symbol", "")
        if symbol in real_symbols:
            cleaned_positions.append(pos)
        else:
            removed.append(symbol)

    if removed:
        print(f"  Ghost positions found: {removed}")

        if not dry_run:
            # Write cleaned positions
            new_data = {
                "positions": cleaned_positions,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            WATCHDOG_POSITIONS.write_text(json.dumps(new_data, indent=2))
            print(f"  Watchdog cleaned - removed {len(removed)} ghost positions")
        else:
            print(f"  [DRY-RUN] Would remove {len(removed)} ghost positions")
    else:
        print("  No ghost positions found")


def log_emergency_action(action: Dict):
    """Log emergency action to JSONL."""
    EMERGENCY_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(EMERGENCY_LOG, 'a', encoding='utf-8') as f:
        f.write(json.dumps(action, ensure_ascii=False) + '\n')


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Emergency Position Fix")
    parser.add_argument("--execute", action="store_true", help="Actually execute (default is dry-run)")
    parser.add_argument("--close-all", action="store_true", help="Close ALL positions")
    parser.add_argument("--symbol", type=str, help="Close specific symbol (e.g., ARDRUSDT)")

    args = parser.parse_args()
    dry_run = not args.execute

    print("=" * 60)
    print("  EMERGENCY POSITION FIX")
    print("=" * 60)
    print(f"  Mode: {'EXECUTE' if args.execute else 'DRY-RUN'}")
    print()

    # Get Binance client
    print("[1/4] Connecting to Binance...")
    client = get_binance_client()

    # Get real positions
    print("[2/4] Getting real positions from Binance...")
    real_positions = get_real_positions(client)

    print(f"\n  Real positions on Binance:")
    for symbol, pos in real_positions.items():
        print(f"    {symbol}: {pos['quantity']:.4f} @ ${pos['price']:.6f} = ${pos['value_usdt']:.2f}")

    # Get USDT balance
    account = client.get_account()
    usdt_balance = 0
    for b in account['balances']:
        if b['asset'] == 'USDT':
            usdt_balance = float(b['free'])
            break
    print(f"\n  USDT Balance: ${usdt_balance:.2f}")

    # Clean watchdog
    print("\n[3/4] Cleaning watchdog from ghost positions...")
    clean_watchdog(real_positions, dry_run)

    # Close positions
    print("\n[4/4] Closing positions...")

    actions = []

    if args.close_all:
        # Close all positions
        for symbol, pos in real_positions.items():
            print(f"\n  Closing {symbol}...")
            result = close_position(client, symbol, pos['quantity'], dry_run)
            actions.append(result)

    elif args.symbol:
        # Close specific symbol
        symbol = args.symbol.upper()
        if not symbol.endswith("USDT"):
            symbol = f"{symbol}USDT"

        if symbol in real_positions:
            pos = real_positions[symbol]
            print(f"\n  Closing {symbol}...")
            result = close_position(client, symbol, pos['quantity'], dry_run)
            actions.append(result)
        else:
            print(f"\n  Symbol {symbol} not found in positions")
    else:
        # Find positions that should be closed (stop loss should have triggered)
        # For now, just report
        print("\n  No --close-all or --symbol specified")
        print("  Use --symbol ARDR to close ARDR position")
        print("  Use --close-all to close all positions")

    # Log actions
    if actions and not dry_run:
        for action in actions:
            log_emergency_action(action)

    # Summary
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)

    if dry_run:
        print("\n  This was a DRY-RUN. No changes made.")
        print("  Run with --execute to actually perform actions.")
    else:
        print(f"\n  Actions executed: {len(actions)}")
        for action in actions:
            print(f"    {action['symbol']}: {action['status']}")

    print()


if __name__ == "__main__":
    main()
