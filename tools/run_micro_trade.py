# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T22:55:00Z
# Purpose: Micro Trade CLI wrapper - run $10 spot trade job (fail-closed)
# === END SIGNATURE ===
"""
Micro Trade CLI - Run $10 Spot Trade Job.

Usage:
    python tools/run_micro_trade.py --dry              # DRY run (no API calls)
    python tools/run_micro_trade.py --testnet          # TESTNET (real API, fake money)
    python tools/run_micro_trade.py --mainnet --confirm # MAINNET (REAL MONEY)

Configuration: config/trade_micro.json

Exit codes:
    0 = Trade completed (profit or timeout exit)
    1 = Trade failed
    2 = Config/setup error
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.trade.micro_trade_executor import (
    MicroTradeExecutor,
    TradeConfig,
    TradeMode,
    TradeState,
    load_config_from_file,
)
from core.exchange.binance_spot_client import (
    BinanceSpotClient,
    BinanceEnv,
    create_client,
)

DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "trade_micro.json"


def main() -> int:
    """Main entrypoint."""
    parser = argparse.ArgumentParser(
        description="Micro Trade CLI - $10 spot trade job (fail-closed)",
    )

    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--dry", action="store_true",
        help="DRY run - simulate without API calls",
    )
    mode_group.add_argument(
        "--testnet", action="store_true",
        help="TESTNET - real API calls with test funds",
    )
    mode_group.add_argument(
        "--mainnet", action="store_true",
        help="MAINNET - REAL MONEY (requires --confirm)",
    )

    parser.add_argument(
        "--confirm", action="store_true",
        help="Confirm MAINNET execution (required for --mainnet)",
    )
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH,
        help=f"Config file path (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--symbol", type=str,
        help="Override symbol from config",
    )
    parser.add_argument(
        "--amount", type=float,
        help="Override quote amount from config",
    )
    parser.add_argument(
        "--resume", type=str,
        help="Resume existing job by job_id",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output JSON result",
    )

    args = parser.parse_args()

    # Determine mode
    if args.dry:
        mode = TradeMode.DRY
    elif args.testnet:
        mode = TradeMode.TESTNET
    elif args.mainnet:
        mode = TradeMode.MAINNET
        if not args.confirm:
            print("ERROR: MAINNET requires --confirm flag (REAL MONEY)", file=sys.stderr)
            print("Usage: python tools/run_micro_trade.py --mainnet --confirm", file=sys.stderr)
            return 2

    # Load config
    try:
        config = load_config_from_file(args.config)
    except Exception as e:
        print(f"ERROR: Failed to load config: {e}", file=sys.stderr)
        return 2

    # Override config values
    config.mode = mode

    if args.symbol:
        config.symbol = args.symbol
    if args.amount:
        config.quote_amount = args.amount
    if args.resume:
        config.job_id = args.resume

    # Print header
    if not args.json:
        print("=" * 60)
        print("MICRO TRADE EXECUTOR v1.0")
        print("=" * 60)
        print(f"Mode: {mode.value}")
        print(f"Symbol: {config.symbol}")
        print(f"Amount: {config.quote_amount} USDT")
        print(f"TP: {config.tp_percent}%")
        print(f"Timeout: {config.timeout_minutes} minutes")
        print(f"Job ID: {config.job_id}")
        print()

        if mode == TradeMode.MAINNET:
            print("WARNING: MAINNET MODE - REAL MONEY AT RISK")
            print()

    # Create client (None for DRY mode)
    client = None
    if mode != TradeMode.DRY:
        try:
            env = BinanceEnv.TESTNET if mode == TradeMode.TESTNET else BinanceEnv.MAINNET
            client = create_client(env)

            # Verify connectivity
            server_time = client.get_server_time()
            if not args.json:
                print(f"Binance {env.value} connected (server time: {server_time})")
                print()
        except Exception as e:
            print(f"ERROR: Failed to connect to Binance: {e}", file=sys.stderr)
            return 2

    # Create and run executor
    try:
        executor = MicroTradeExecutor(config, client)

        if not args.json:
            print(f"Starting from state: {executor.get_current_state().value}")
            print()

        result = executor.run()

        # Output result
        if args.json:
            output = {
                "job_id": result.job_id,
                "state": result.state,
                "outcome": result.outcome,
                "pnl_usdt": result.pnl_usdt,
                "error": result.error,
                "buy_price": result.buy_price,
                "buy_qty": result.buy_qty,
                "tp_price": result.tp_price,
                "exit_price": result.exit_price,
            }
            print(json.dumps(output, indent=2))
        else:
            print("=" * 60)
            print("RESULT")
            print("=" * 60)
            print(f"Final State: {result.state}")
            print(f"Outcome: {result.outcome or 'N/A'}")
            print()

            if result.buy_price:
                print(f"Buy Price: {result.buy_price:.8f}")
                print(f"Buy Qty: {result.buy_qty:.8f}")

            if result.tp_price:
                print(f"TP Price: {result.tp_price:.8f}")

            if result.exit_price:
                print(f"Exit Price: {result.exit_price:.8f}")

            if result.pnl_usdt is not None:
                pnl_sign = "+" if result.pnl_usdt >= 0 else ""
                print(f"PnL: {pnl_sign}{result.pnl_usdt:.4f} USDT")

            if result.error:
                print(f"Error: {result.error}")

            print()
            print(f"State file: state/trade/micro_trade_{result.job_id}.json")
            print(f"Audit log: state/trade/micro_trade_audit.jsonl")

        # Exit code
        if result.state in ("DONE_PROFIT", "DONE_TIMEOUT_EXIT"):
            return 0
        else:
            return 1

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
