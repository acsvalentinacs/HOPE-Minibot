# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T13:00:00Z
# Purpose: Run actual TESTNET trade test
# === END SIGNATURE ===
"""
TESTNET Trade Test.

Executes a real $10 USDT trade on Binance Testnet.
Requires TESTNET API keys in C:\secrets\hope.env
"""
from __future__ import annotations

import io
import sys
import os

# UTF-8 for Windows
if sys.stdout and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr and hasattr(sys.stderr, 'buffer'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from pathlib import Path


def load_secrets() -> dict:
    """Load secrets from env file."""
    secrets_path = Path(r"C:\secrets\hope.env")
    env = {}

    if not secrets_path.exists():
        print(f"ERROR: Secrets file not found: {secrets_path}")
        return env

    for line in secrets_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()

    return env


def check_testnet_keys(env: dict) -> bool:
    """Check if TESTNET keys are present."""
    required = ["BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET"]
    missing = [k for k in required if not env.get(k)]

    if missing:
        print(f"MISSING TESTNET KEYS: {missing}")
        print()
        print("To get TESTNET keys:")
        print("1. Go to https://testnet.binance.vision/")
        print("2. Login with GitHub")
        print("3. Generate API Key")
        print("4. Add to C:\\secrets\\hope.env:")
        print("   BINANCE_TESTNET_API_KEY=your_key")
        print("   BINANCE_TESTNET_API_SECRET=your_secret")
        return False

    print("TESTNET keys: FOUND")
    print(f"  API Key: {env['BINANCE_TESTNET_API_KEY'][:8]}...")
    return True


def test_connection(env: dict) -> bool:
    """Test connection to Binance Testnet."""
    print()
    print("Testing Testnet connection...")

    try:
        from core.spot_testnet_client import SpotTestnetClient

        client = SpotTestnetClient(
            api_key=env.get("BINANCE_TESTNET_API_KEY", ""),
            api_secret=env.get("BINANCE_TESTNET_API_SECRET", ""),
        )

        # Test ticker
        ticker = client.get_ticker_price("BTCUSDT")
        if ticker:
            price = float(ticker[0]["price"]) if isinstance(ticker, list) else float(ticker.get("price", 0))
            print(f"  BTCUSDT price: ${price:,.2f}")

        # Test account
        account = client.get_account()
        if account:
            balances = {b["asset"]: float(b["free"]) for b in account.get("balances", []) if float(b["free"]) > 0}
            print(f"  Balances: {balances}")

            usdt_balance = balances.get("USDT", 0)
            if usdt_balance < 10:
                print(f"  WARNING: USDT balance ({usdt_balance}) < 10. Need more testnet funds.")
                print("  Get testnet funds at: https://testnet.binance.vision/")
                return False

        print("  Connection: OK")
        return True

    except Exception as e:
        print(f"  Connection FAILED: {e}")
        return False


def run_testnet_trade(env: dict) -> int:
    """Run actual testnet trade."""
    print()
    print("=" * 50)
    print("EXECUTING TESTNET TRADE")
    print("=" * 50)

    from core.trade.micro_trade_executor import MicroTradeExecutor, TradeConfig, TradeMode
    from core.spot_testnet_client import SpotTestnetClient

    # Create testnet client
    client = SpotTestnetClient(
        api_key=env.get("BINANCE_TESTNET_API_KEY", ""),
        api_secret=env.get("BINANCE_TESTNET_API_SECRET", ""),
    )

    # Configure trade
    cfg = TradeConfig(
        symbol="BTCUSDT",
        quote_amount=10.0,  # $10 USDT
        tp_percent=0.5,     # 0.5% take profit
        timeout_minutes=5,  # 5 min timeout
        mode=TradeMode.TESTNET,
    )

    print(f"Config:")
    print(f"  Symbol: {cfg.symbol}")
    print(f"  Amount: ${cfg.quote_amount}")
    print(f"  TP: {cfg.tp_percent}%")
    print(f"  Timeout: {cfg.timeout_minutes} min")
    print(f"  Mode: {cfg.mode.value}")
    print(f"  Job ID: {cfg.job_id}")
    print()

    # Execute
    executor = MicroTradeExecutor(cfg, client=client)

    print("Running state machine...")
    result = executor.run()

    print()
    print("-" * 50)
    print(f"Final State: {result.state}")
    print(f"Outcome: {result.outcome}")

    if result.buy_order_id:
        print(f"Buy Order ID: {result.buy_order_id}")
    if result.buy_price:
        print(f"Buy Price: ${result.buy_price:,.2f}")
    if result.buy_qty:
        print(f"Buy Qty: {result.buy_qty:.8f} BTC")
    if result.tp_price:
        print(f"TP Price: ${result.tp_price:,.2f}")
    if result.pnl_usdt is not None:
        print(f"PnL: ${result.pnl_usdt:.4f}")
    if result.error:
        print(f"Error: {result.error}")

    print()
    print("Transitions:")
    for t in result.transitions:
        print(f"  {t['from']} -> {t['to']}")
        print(f"    {t['reason']}")

    print()
    if result.outcome in ("profit", "timeout_exit"):
        print("RESULT: PASS - TESTNET trade completed")
        return 0
    elif result.state == "TP_SELL_PLACED":
        print("RESULT: IN_PROGRESS - TP order waiting")
        print(f"  Check state file: state/trade/micro_trade_{cfg.job_id}.json")
        return 0
    else:
        print(f"RESULT: FAIL - {result.error or result.state}")
        return 1


def main():
    print("=" * 50)
    print("BINANCE TESTNET TRADE TEST")
    print("=" * 50)
    print()

    # Load secrets
    env = load_secrets()
    if not env:
        return 1

    # Check keys
    if not check_testnet_keys(env):
        return 1

    # Test connection
    if not test_connection(env):
        return 1

    # Run trade
    return run_testnet_trade(env)


if __name__ == "__main__":
    sys.exit(main())
