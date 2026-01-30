# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-01-30T02:30:00Z
# Purpose: Inject test signal to verify full trading cycle
# Contract: Creates signal → triggers AI pipeline → verifies execution
# === END SIGNATURE ===
"""
Inject Test Signal - Verify Full Trading Cycle

This script injects a test signal into the MoonBot signal file
to verify the complete pipeline:

    Signal File → MoonBot Live → Decisions → Production Engine → Binance

Usage:
    python scripts/inject_test_signal.py [--symbol BTCUSDT] [--buys 1000]
"""

import argparse
import json
import hashlib
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("INJECT")

# Paths
SIGNALS_DIR = Path("data/moonbot_signals")
STATE_SIGNALS_DIR = Path("state/ai/signals")

def generate_signal(
    symbol: str = "BTCUSDT",
    buys_per_sec: int = 1000,
    delta_pct: float = 5.0,
    vol_raise_pct: float = 200,
    strategy: str = "PumpDetection"
) -> dict:
    """Generate a test signal."""
    ts = datetime.now(timezone.utc).isoformat()

    signal = {
        "timestamp": ts,
        "symbol": symbol,
        "price": 104500.0 if symbol == "BTCUSDT" else 2.5,
        "delta_pct": delta_pct,
        "buys_per_sec": buys_per_sec,
        "vol_per_sec": buys_per_sec * 3,
        "vol_raise_pct": vol_raise_pct,
        "daily_volume": 5000000000 if symbol == "BTCUSDT" else 100000000,
        "dbtc_5m": 2.5,
        "dbtc_1m": 1.0,
        "strategy": strategy,
        "direction": "Long",
        "source": "test_injection"
    }

    # Add sha256
    canonical = json.dumps(signal, sort_keys=True, separators=(",", ":"))
    signal["sha256"] = hashlib.sha256(canonical.encode()).hexdigest()[:16]

    return signal


def inject_to_file(signal: dict, path: Path):
    """Inject signal to JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(signal) + "\n")

    logger.info(f"Injected to: {path}")


def main():
    parser = argparse.ArgumentParser(description="Inject test signal")
    parser.add_argument("--symbol", type=str, default="BTCUSDT", help="Trading pair")
    parser.add_argument("--buys", type=int, default=1000, help="Buys per second")
    parser.add_argument("--delta", type=float, default=5.0, help="Delta percent")
    parser.add_argument("--vol", type=float, default=200, help="Volume raise percent")
    parser.add_argument("--strategy", type=str, default="PumpDetection", help="Strategy name")
    parser.add_argument("--dry-run", action="store_true", help="Show signal without injecting")
    args = parser.parse_args()

    # Generate signal
    signal = generate_signal(
        symbol=args.symbol,
        buys_per_sec=args.buys,
        delta_pct=args.delta,
        vol_raise_pct=args.vol,
        strategy=args.strategy
    )

    logger.info(f"Generated signal: {args.symbol}")
    logger.info(f"  buys_per_sec: {args.buys}")
    logger.info(f"  delta_pct: {args.delta}")
    logger.info(f"  vol_raise_pct: {args.vol}")
    logger.info(f"  strategy: {args.strategy}")

    if args.dry_run:
        print(json.dumps(signal, indent=2))
        return

    # Inject to daily signal file
    today = datetime.now().strftime("%Y%m%d")
    daily_file = SIGNALS_DIR / f"signals_{today}.jsonl"
    inject_to_file(signal, daily_file)

    # Also inject to state/ai/signals for moonbot_live
    state_file = STATE_SIGNALS_DIR / "moonbot_signals.jsonl"
    inject_to_file(signal, state_file)

    logger.info("=" * 50)
    logger.info("Signal injected! Watch for:")
    logger.info("  1. AI decision in state/ai/decisions.jsonl")
    logger.info("  2. Trade execution in Production Engine logs")
    logger.info("=" * 50)


if __name__ == "__main__":
    main()
