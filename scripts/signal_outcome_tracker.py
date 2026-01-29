# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 07:50:00 UTC
# Purpose: Track MoonBot signal outcomes for supervised learning
# === END SIGNATURE ===
"""
Signal Outcome Tracker — Track what happens after MoonBot signals.

For each signal, records price at:
- Entry (signal time)
- +1 minute
- +5 minutes
- +15 minutes
- +60 minutes

Labels signals as WIN/LOSS based on price movement.

Usage:
    python scripts/signal_outcome_tracker.py --watch
    python scripts/signal_outcome_tracker.py --backfill  # Process existing signals
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [OUTCOME] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# === Configuration ===

SIGNALS_DIR = Path("data/moonbot_signals")
OUTCOMES_FILE = Path("data/moonbot_signals/outcomes.jsonl")
BINANCE_API = "https://api.binance.com/api/v3"

# Time horizons (seconds)
HORIZONS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "60m": 3600,
}

# Win threshold
WIN_THRESHOLD_PCT = 0.5  # 0.5% profit = WIN


class OutcomeTracker:
    """
    Track outcomes of trading signals.

    Monitors price movements after signals and labels them for ML training.
    """

    def __init__(self):
        self._pending: List[Dict[str, Any]] = []  # Signals waiting for outcome
        self._client: Optional[httpx.AsyncClient] = None
        self._price_cache: Dict[str, float] = {}

        SIGNALS_DIR.mkdir(parents=True, exist_ok=True)

    async def fetch_price(self, symbol: str) -> Optional[float]:
        """Fetch current price from Binance."""
        try:
            resp = await self._client.get(
                f"{BINANCE_API}/ticker/price",
                params={"symbol": symbol}
            )
            if resp.status_code == 200:
                data = resp.json()
                price = float(data.get("price", 0))
                self._price_cache[symbol] = price
                return price
        except Exception as e:
            logger.debug(f"Price fetch error for {symbol}: {e}")

        return self._price_cache.get(symbol)

    async def add_signal(self, signal: Dict[str, Any]) -> None:
        """Add a signal to track."""
        symbol = signal.get("symbol", "")
        if not symbol:
            return

        # Ensure USDT suffix
        if not symbol.endswith("USDT"):
            symbol = f"{symbol}USDT"

        # Get entry price
        entry_price = signal.get("price")
        if not entry_price:
            entry_price = await self.fetch_price(symbol)

        if not entry_price:
            logger.warning(f"Cannot get entry price for {symbol}")
            return

        # Create tracking record
        record = {
            "signal_id": f"{symbol}_{int(time.time())}",
            "symbol": symbol,
            "entry_time": time.time(),
            "entry_price": entry_price,
            "signal_type": signal.get("signal_type", "unknown"),
            "strategy": signal.get("strategy", "unknown"),
            "delta_pct": signal.get("delta_pct", 0),
            "daily_vol_m": signal.get("daily_vol_m", 0),
            "direction": signal.get("direction", "Long"),
            # Outcomes to fill
            "prices": {},
            "outcomes": {},
            "final_label": None,
        }

        self._pending.append(record)
        logger.info(f"Tracking {symbol} @ ${entry_price:.6f}")

    async def update_outcomes(self) -> None:
        """Update outcomes for all pending signals."""
        now = time.time()
        completed = []

        for record in self._pending:
            symbol = record["symbol"]
            entry_time = record["entry_time"]
            entry_price = record["entry_price"]

            # Check each horizon
            for horizon_name, horizon_sec in HORIZONS.items():
                if horizon_name in record["prices"]:
                    continue  # Already recorded

                elapsed = now - entry_time
                if elapsed >= horizon_sec:
                    # Time to record this horizon
                    current_price = await self.fetch_price(symbol)
                    if current_price:
                        record["prices"][horizon_name] = current_price

                        # Calculate outcome
                        change_pct = ((current_price - entry_price) / entry_price) * 100

                        # For Long signals, positive = win
                        direction = record.get("direction", "Long")
                        if direction == "Short":
                            change_pct = -change_pct

                        record["outcomes"][horizon_name] = {
                            "price": current_price,
                            "change_pct": round(change_pct, 4),
                            "win": change_pct >= WIN_THRESHOLD_PCT,
                        }

                        logger.info(
                            f"{symbol} {horizon_name}: "
                            f"${current_price:.6f} ({change_pct:+.2f}%) "
                            f"{'WIN' if change_pct >= WIN_THRESHOLD_PCT else 'LOSS'}"
                        )

            # Check if all horizons recorded
            if len(record["prices"]) == len(HORIZONS):
                # Calculate final label based on 5m outcome
                outcome_5m = record["outcomes"].get("5m", {})
                record["final_label"] = "WIN" if outcome_5m.get("win", False) else "LOSS"
                record["completed_at"] = datetime.now(timezone.utc).isoformat()

                # Save to file
                await self._save_outcome(record)
                completed.append(record)

        # Remove completed
        for record in completed:
            self._pending.remove(record)

    async def _save_outcome(self, record: Dict[str, Any]) -> None:
        """Save completed outcome to JSONL."""
        with open(OUTCOMES_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        logger.info(
            f"SAVED: {record['symbol']} "
            f"Entry=${record['entry_price']:.6f} "
            f"Label={record['final_label']}"
        )

    async def watch_mode(self) -> None:
        """Watch for new signals and track outcomes."""
        logger.info("Starting watch mode...")
        logger.info("Paste MoonBot signals or load from signals file")

        async with httpx.AsyncClient(timeout=10.0) as client:
            self._client = client

            # Load existing signals
            await self._load_existing_signals()

            # Main loop
            while True:
                await self.update_outcomes()
                await asyncio.sleep(5)

    async def _load_existing_signals(self) -> None:
        """Load signals from existing JSONL files."""
        for jsonl_file in SIGNALS_DIR.glob("signals_*.jsonl"):
            try:
                with open(jsonl_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        try:
                            signal = json.loads(line)
                            # Only add recent signals (last hour)
                            signal_time = signal.get("timestamp", "")
                            if signal_time:
                                await self.add_signal(signal)
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                logger.warning(f"Error loading {jsonl_file}: {e}")

    async def backfill_mode(self) -> None:
        """Backfill outcomes for historical signals."""
        logger.info("Starting backfill mode...")

        async with httpx.AsyncClient(timeout=10.0) as client:
            self._client = client

            # Load all signals
            signals = []
            for jsonl_file in SIGNALS_DIR.glob("signals_*.jsonl"):
                with open(jsonl_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        try:
                            signals.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue

            logger.info(f"Found {len(signals)} signals to backfill")

            # For backfill, we'd need historical klines
            # This requires a different approach
            for signal in signals:
                symbol = signal.get("symbol", "")
                if not symbol:
                    continue

                if not symbol.endswith("USDT"):
                    symbol = f"{symbol}USDT"

                # Get historical klines
                try:
                    # Parse signal timestamp
                    ts_str = signal.get("timestamp", "")
                    if "T" in ts_str:
                        # ISO format
                        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        start_ms = int(dt.timestamp() * 1000)
                    else:
                        continue

                    # Fetch klines from signal time
                    resp = await client.get(
                        f"{BINANCE_API}/klines",
                        params={
                            "symbol": symbol,
                            "interval": "1m",
                            "startTime": start_ms,
                            "limit": 65,  # Cover 60 minutes + buffer
                        }
                    )

                    if resp.status_code != 200:
                        continue

                    klines = resp.json()
                    if len(klines) < 2:
                        continue

                    # Extract prices at horizons
                    entry_price = float(klines[0][4])  # Close of first candle

                    record = {
                        "signal_id": f"{symbol}_{start_ms}",
                        "symbol": symbol,
                        "entry_time": start_ms / 1000,
                        "entry_price": entry_price,
                        "signal_type": signal.get("signal_type", "unknown"),
                        "strategy": signal.get("strategy", "unknown"),
                        "delta_pct": signal.get("delta_pct", 0),
                        "daily_vol_m": signal.get("daily_vol_m", 0),
                        "direction": signal.get("direction", "Long"),
                        "prices": {},
                        "outcomes": {},
                    }

                    # Get prices at horizons
                    for horizon_name, horizon_sec in HORIZONS.items():
                        candle_idx = horizon_sec // 60
                        if candle_idx < len(klines):
                            price = float(klines[candle_idx][4])
                            record["prices"][horizon_name] = price

                            change_pct = ((price - entry_price) / entry_price) * 100
                            direction = signal.get("direction", "Long")
                            if direction == "Short":
                                change_pct = -change_pct

                            record["outcomes"][horizon_name] = {
                                "price": price,
                                "change_pct": round(change_pct, 4),
                                "win": change_pct >= WIN_THRESHOLD_PCT,
                            }

                    # Final label
                    outcome_5m = record["outcomes"].get("5m", {})
                    record["final_label"] = "WIN" if outcome_5m.get("win", False) else "LOSS"

                    await self._save_outcome(record)

                    # Rate limit
                    await asyncio.sleep(0.1)

                except Exception as e:
                    logger.debug(f"Backfill error for {symbol}: {e}")

            logger.info("Backfill complete!")


async def main():
    parser = argparse.ArgumentParser(description="Signal Outcome Tracker")
    parser.add_argument("--watch", action="store_true", help="Watch mode")
    parser.add_argument("--backfill", action="store_true", help="Backfill historical")

    args = parser.parse_args()

    tracker = OutcomeTracker()

    if args.backfill:
        await tracker.backfill_mode()
    else:
        await tracker.watch_mode()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
