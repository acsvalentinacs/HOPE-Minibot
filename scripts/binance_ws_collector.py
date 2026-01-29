# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 07:45:00 UTC
# Purpose: Real-time Binance WebSocket data collector for AI training
# === END SIGNATURE ===
"""
Binance WebSocket Collector — Real-time market data for AI training.

Collects:
- Trade streams (buys/sells per second)
- Mini ticker (price changes)
- Aggregated trades

Usage:
    python scripts/binance_ws_collector.py --hours 1
    python scripts/binance_ws_collector.py --symbols BTCUSDT,ETHUSDT --hours 2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import websockets
    WS_AVAILABLE = True
except ImportError:
    WS_AVAILABLE = False
    print("Install: pip install websockets")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WS] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# === Configuration ===

BINANCE_WS_URL = "wss://stream.binance.com:9443/ws"
OUTPUT_DIR = Path("data/binance_realtime")

# Top trading pairs for monitoring
DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT",
    "LINKUSDT", "UNIUSDT", "ATOMUSDT", "LTCUSDT", "ETCUSDT",
]


class BinanceWSCollector:
    """
    Real-time data collector from Binance WebSocket.

    Collects trade data and aggregates into features for AI training.
    """

    def __init__(
        self,
        symbols: List[str],
        hours: float = 1.0,
        output_dir: Path = OUTPUT_DIR,
    ):
        self.symbols = [s.lower() for s in symbols]
        self.hours = hours
        self.output_dir = output_dir
        self.end_time = time.time() + (hours * 3600)

        self._stop_event = asyncio.Event()
        self._ws = None

        # Data buffers (per symbol)
        self._trades: Dict[str, List[Dict]] = {s: [] for s in self.symbols}
        self._last_prices: Dict[str, float] = {}
        self._buys_count: Dict[str, int] = {s: 0 for s in self.symbols}
        self._sells_count: Dict[str, int] = {s: 0 for s in self.symbols}
        self._volume: Dict[str, float] = {s: 0.0 for s in self.symbols}

        # Statistics
        self.stats = {
            "start_time": datetime.now(timezone.utc).isoformat(),
            "trades_collected": 0,
            "snapshots_saved": 0,
            "symbols": len(symbols),
        }

        # Ensure output dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def start(self) -> None:
        """Start collecting data."""
        if not WS_AVAILABLE:
            logger.error("websockets not installed!")
            return

        logger.info("=" * 60)
        logger.info("BINANCE WEBSOCKET COLLECTOR STARTING")
        logger.info(f"Symbols: {len(self.symbols)}")
        logger.info(f"Duration: {self.hours} hours")
        logger.info("=" * 60)

        # Build subscription message
        streams = []
        for symbol in self.symbols:
            streams.append(f"{symbol}@aggTrade")  # Aggregated trades

        subscribe_msg = {
            "method": "SUBSCRIBE",
            "params": streams,
            "id": 1
        }

        try:
            async with websockets.connect(BINANCE_WS_URL) as ws:
                self._ws = ws

                # Subscribe to streams
                await ws.send(json.dumps(subscribe_msg))
                logger.info(f"Subscribed to {len(streams)} streams")

                # Start tasks
                tasks = [
                    asyncio.create_task(self._receive_loop()),
                    asyncio.create_task(self._snapshot_loop()),
                    asyncio.create_task(self._progress_loop()),
                ]

                # Wait for completion
                while not self._stop_event.is_set() and time.time() < self.end_time:
                    await asyncio.sleep(1)

                # Cleanup
                self._stop_event.set()
                for task in tasks:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

        except Exception as e:
            logger.error(f"WebSocket error: {e}")

        # Final save
        await self._save_final_report()

    async def _receive_loop(self) -> None:
        """Receive and process WebSocket messages."""
        while not self._stop_event.is_set():
            try:
                msg = await asyncio.wait_for(self._ws.recv(), timeout=5.0)
                data = json.loads(msg)

                # Skip subscription confirmations
                if "result" in data or "id" in data:
                    continue

                # Process trade
                if "e" in data and data["e"] == "aggTrade":
                    self._process_trade(data)

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Receive error: {e}")

    def _process_trade(self, data: Dict[str, Any]) -> None:
        """Process aggregated trade message."""
        symbol = data.get("s", "").lower()
        if symbol not in self.symbols:
            return

        price = float(data.get("p", 0))
        qty = float(data.get("q", 0))
        is_buyer_maker = data.get("m", False)  # True = sell, False = buy

        # Update counters
        if is_buyer_maker:
            self._sells_count[symbol] += 1
        else:
            self._buys_count[symbol] += 1

        self._volume[symbol] += price * qty
        self._last_prices[symbol] = price

        # Store trade
        trade = {
            "ts": data.get("T", 0),
            "p": price,
            "q": qty,
            "buy": not is_buyer_maker,
        }
        self._trades[symbol].append(trade)

        # Limit buffer size
        if len(self._trades[symbol]) > 1000:
            self._trades[symbol] = self._trades[symbol][-500:]

        self.stats["trades_collected"] += 1

    async def _snapshot_loop(self) -> None:
        """Save snapshots every 10 seconds."""
        while not self._stop_event.is_set():
            await asyncio.sleep(10)

            try:
                await self._save_snapshot()
            except Exception as e:
                logger.error(f"Snapshot error: {e}")

    async def _save_snapshot(self) -> None:
        """Save current state as snapshot for training."""
        timestamp = datetime.now(timezone.utc).isoformat()

        snapshots = []
        for symbol in self.symbols:
            if symbol not in self._last_prices:
                continue

            trades = self._trades.get(symbol, [])

            # Calculate features
            buys = self._buys_count.get(symbol, 0)
            sells = self._sells_count.get(symbol, 0)
            total = buys + sells

            # Buys/sells in last 10 seconds
            recent_trades = [t for t in trades if t.get("ts", 0) > (time.time() - 10) * 1000]
            recent_buys = sum(1 for t in recent_trades if t.get("buy"))
            recent_sells = len(recent_trades) - recent_buys

            snapshot = {
                "timestamp": timestamp,
                "symbol": symbol.upper(),
                "price": self._last_prices.get(symbol, 0),
                "buys_total": buys,
                "sells_total": sells,
                "buys_10s": recent_buys,
                "sells_10s": recent_sells,
                "trades_10s": len(recent_trades),
                "volume_usdt": self._volume.get(symbol, 0),
                "buy_ratio": buys / total if total > 0 else 0.5,
                "buys_per_sec": recent_buys / 10.0,
                "sells_per_sec": recent_sells / 10.0,
            }
            snapshots.append(snapshot)

        # Save to JSONL
        if snapshots:
            date_str = datetime.now().strftime("%Y%m%d")
            output_file = self.output_dir / f"snapshots_{date_str}.jsonl"

            with open(output_file, "a", encoding="utf-8") as f:
                for snap in snapshots:
                    f.write(json.dumps(snap, ensure_ascii=False) + "\n")

            self.stats["snapshots_saved"] += len(snapshots)

        # Reset counters for next period
        for symbol in self.symbols:
            self._buys_count[symbol] = 0
            self._sells_count[symbol] = 0
            self._volume[symbol] = 0.0

    async def _progress_loop(self) -> None:
        """Print progress every minute."""
        while not self._stop_event.is_set():
            await asyncio.sleep(60)

            elapsed = time.time() - (self.end_time - self.hours * 3600)
            remaining = max(0, self.end_time - time.time())

            logger.info(
                f"Progress: {int(elapsed/60)}m elapsed, {int(remaining/60)}m remaining | "
                f"Trades: {self.stats['trades_collected']} | "
                f"Snapshots: {self.stats['snapshots_saved']}"
            )

    async def _save_final_report(self) -> None:
        """Save final statistics."""
        self.stats["end_time"] = datetime.now(timezone.utc).isoformat()

        report_file = self.output_dir / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(self.stats, f, indent=2)

        logger.info("=" * 60)
        logger.info("COLLECTION COMPLETE")
        logger.info(f"Trades collected: {self.stats['trades_collected']}")
        logger.info(f"Snapshots saved: {self.stats['snapshots_saved']}")
        logger.info(f"Report: {report_file}")
        logger.info("=" * 60)


async def main():
    parser = argparse.ArgumentParser(description="Binance WebSocket Collector")
    parser.add_argument("--hours", type=float, default=1.0, help="Collection duration")
    parser.add_argument("--symbols", type=str, help="Comma-separated symbols")

    args = parser.parse_args()

    symbols = args.symbols.split(",") if args.symbols else DEFAULT_SYMBOLS

    collector = BinanceWSCollector(
        symbols=symbols,
        hours=args.hours,
    )

    await collector.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
