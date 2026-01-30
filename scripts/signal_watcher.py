# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 15:40:00 UTC
# Purpose: Real-time MoonBot signal watcher → AutoTrader pipeline
# === END SIGNATURE ===
"""
HOPE Signal Watcher - Automatic Signal Pipeline

Monitors MoonBot signal files and forwards to AutoTrader for execution.

FLOW:
    MoonBot signals file (JSONL)
           ↓
    Signal Watcher (this script)
           ↓
    AutoTrader API (:8200/signal)
           ↓
    Eye of God V3 Decision
           ↓
    Order Execution (DRY/LIVE)
           ↓
    Telegram Notification

Usage:
    python scripts/signal_watcher.py --watch
    python scripts/signal_watcher.py --inject-test
"""

import asyncio
import json
import logging
import os
import sys
import time
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Callable
from dataclasses import dataclass, asdict

# Add project root
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    import httpx
except ImportError:
    print("Installing httpx...")
    os.system(f"{sys.executable} -m pip install httpx -q")
    import httpx

try:
    from dotenv import load_dotenv
    load_dotenv(Path("C:/secrets/hope.env"))
except ImportError:
    pass

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
log = logging.getLogger("WATCHER")

# Configuration
SIGNALS_DIR = ROOT / "data" / "moonbot_signals"
AUTOTRADER_URL = "http://127.0.0.1:8200"
STATE_FILE = ROOT / "state" / "watcher_state.json"

# Ensure directories
SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)


@dataclass
class WatcherState:
    """Persistent watcher state."""
    last_processed_file: str = ""
    last_processed_line: int = 0
    processed_signal_ids: List[str] = None
    total_signals_sent: int = 0
    total_signals_skipped: int = 0
    last_update: str = ""

    def __post_init__(self):
        if self.processed_signal_ids is None:
            self.processed_signal_ids = []

    def save(self):
        self.last_update = datetime.now(timezone.utc).isoformat()
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls) -> "WatcherState":
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                return cls(**data)
            except Exception as e:
                log.warning(f"Failed to load state: {e}")
        return cls()


class SignalWatcher:
    """
    Watches MoonBot signal files and forwards to AutoTrader.

    Features:
    - Tail-like file monitoring (new signals only)
    - Deduplication by signal_id
    - Auto-reconnect to AutoTrader
    - State persistence across restarts
    - Telegram notifications
    """

    def __init__(
        self,
        signals_dir: Path = SIGNALS_DIR,
        autotrader_url: str = AUTOTRADER_URL,
        poll_interval: float = 0.5,
    ):
        self.signals_dir = signals_dir
        self.autotrader_url = autotrader_url
        self.poll_interval = poll_interval

        self.state = WatcherState.load()
        self.running = False
        self.client: Optional[httpx.AsyncClient] = None
        self.telegram: Optional["TelegramSender"] = None

        # Signal dedup (last 1000)
        self._seen_ids: Set[str] = set(self.state.processed_signal_ids[-1000:])

        # Stats
        self.session_signals = 0
        self.session_trades = 0

    async def start(self):
        """Start watching for signals."""
        self.running = True
        self.client = httpx.AsyncClient(timeout=10.0)

        # Try to init Telegram
        try:
            from core.telegram_sender import TelegramSender
            self.telegram = TelegramSender()
            log.info("Telegram notifications enabled")
        except Exception as e:
            log.warning(f"Telegram not available: {e}")

        log.info(f"Signal Watcher started")
        log.info(f"  Signals dir: {self.signals_dir}")
        log.info(f"  AutoTrader: {self.autotrader_url}")
        log.info(f"  Seen signals: {len(self._seen_ids)}")

        # Notify start
        if self.telegram:
            await self.telegram.send("🟢 Signal Watcher started\nMonitoring MoonBot signals...")

        try:
            await self._watch_loop()
        finally:
            await self.stop()

    async def stop(self):
        """Stop watcher and save state."""
        self.running = False

        # Save state
        self.state.processed_signal_ids = list(self._seen_ids)[-1000:]
        self.state.save()

        if self.client:
            await self.client.aclose()
        if self.telegram:
            await self.telegram.close()

        log.info(f"Signal Watcher stopped. Session: {self.session_signals} signals, {self.session_trades} trades")

    async def _watch_loop(self):
        """Main watch loop."""
        while self.running:
            try:
                # Get today's signal file
                today = datetime.now(timezone.utc).strftime("%Y%m%d")
                signal_file = self.signals_dir / f"signals_{today}.jsonl"

                if signal_file.exists():
                    await self._process_file(signal_file)

                await asyncio.sleep(self.poll_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Watch loop error: {e}")
                await asyncio.sleep(1)

    async def _process_file(self, signal_file: Path):
        """Process new signals from file."""
        try:
            lines = signal_file.read_text(encoding="utf-8").strip().split("\n")
        except Exception as e:
            log.warning(f"Failed to read {signal_file}: {e}")
            return

        # Determine starting line
        start_line = 0
        if str(signal_file) == self.state.last_processed_file:
            start_line = self.state.last_processed_line
        else:
            # New file - process from beginning but skip old signals
            self.state.last_processed_file = str(signal_file)
            self.state.last_processed_line = 0

        # Process new lines
        new_signals = 0
        for i, line in enumerate(lines[start_line:], start=start_line):
            if not line.strip():
                continue

            try:
                signal = json.loads(line)

                # Generate signal ID if missing
                signal_id = signal.get("signal_id") or signal.get("sha256")
                if not signal_id:
                    signal_id = hashlib.sha256(line.encode()).hexdigest()[:16]

                # Skip duplicates
                if signal_id in self._seen_ids:
                    continue

                # Check signal age (max 60 seconds)
                signal_ts = signal.get("timestamp")
                if signal_ts:
                    try:
                        if isinstance(signal_ts, str):
                            if "+" in signal_ts or signal_ts.endswith("Z"):
                                ts = datetime.fromisoformat(signal_ts.replace("Z", "+00:00"))
                            else:
                                ts = datetime.fromisoformat(signal_ts).replace(tzinfo=timezone.utc)
                        else:
                            ts = datetime.fromtimestamp(signal_ts, tz=timezone.utc)

                        age = (datetime.now(timezone.utc) - ts).total_seconds()
                        if age > 60:
                            log.debug(f"Signal too old ({age:.0f}s): {signal.get('symbol')}")
                            self._seen_ids.add(signal_id)
                            continue
                    except Exception as e:
                        log.warning(f"Failed to parse timestamp: {e}")

                # Forward to AutoTrader
                success = await self._forward_signal(signal)

                # Mark as seen
                self._seen_ids.add(signal_id)
                new_signals += 1

                if success:
                    self.session_signals += 1
                    self.state.total_signals_sent += 1
                else:
                    self.state.total_signals_skipped += 1

            except json.JSONDecodeError as e:
                log.warning(f"Invalid JSON on line {i}: {e}")
            except Exception as e:
                log.error(f"Error processing signal: {e}")

        # Update state
        self.state.last_processed_line = len(lines)

        if new_signals > 0:
            log.info(f"Processed {new_signals} new signals from {signal_file.name}")
            self.state.save()

    async def _forward_signal(self, signal: Dict) -> bool:
        """Forward signal to AutoTrader API."""
        try:
            # Prepare signal data
            data = {
                "symbol": signal.get("symbol", ""),
                "timestamp": signal.get("timestamp", datetime.now(timezone.utc).isoformat()),
                "strategy": signal.get("strategy", "MoonBot"),
                "direction": signal.get("direction", "Long"),
                "price": float(signal.get("price", 0)),
                "buys_per_sec": float(signal.get("buys_per_sec", 0)),
                "delta_pct": float(signal.get("delta_pct", 0)),
                "vol_raise_pct": float(signal.get("vol_raise_pct", 0)),
                "daily_volume_m": float(signal.get("daily_volume_m", signal.get("daily_volume", 0)) or 100),
            }

            # Send to AutoTrader
            response = await self.client.post(
                f"{self.autotrader_url}/signal",
                json=data,
                timeout=5.0
            )

            if response.status_code == 200:
                result = response.json()
                log.info(f"Signal forwarded: {data['symbol']} | buys={data['buys_per_sec']:.1f}/s | queue={result.get('queue_size', '?')}")

                # Check if trade was executed
                await asyncio.sleep(0.5)
                status = await self._get_status()
                if status and status.get("stats", {}).get("signals_traded", 0) > self.session_trades:
                    self.session_trades = status["stats"]["signals_traded"]
                    await self._notify_trade(data, status)

                return True
            else:
                log.warning(f"AutoTrader rejected signal: {response.status_code}")
                return False

        except httpx.ConnectError:
            log.error("AutoTrader not available - is it running?")
            return False
        except Exception as e:
            log.error(f"Forward error: {e}")
            return False

    async def _get_status(self) -> Optional[Dict]:
        """Get AutoTrader status."""
        try:
            response = await self.client.get(f"{self.autotrader_url}/status", timeout=2.0)
            if response.status_code == 200:
                return response.json()
        except:
            pass
        return None

    async def _notify_trade(self, signal: Dict, status: Dict):
        """Send Telegram notification for trade."""
        if not self.telegram:
            return

        try:
            stats = status.get("stats", {})
            msg = f"""🔥 TRADE EXECUTED

Symbol: {signal['symbol']}
Strategy: {signal['strategy']}
Buys/sec: {signal['buys_per_sec']:.1f}
Delta: {signal['delta_pct']:.1f}%

Session stats:
• Signals: {stats.get('signals_received', 0)}
• Traded: {stats.get('signals_traded', 0)}
• Skipped: {stats.get('signals_skipped', 0)}
• Open positions: {status.get('open_positions', 0)}"""

            await self.telegram.send(msg)
        except Exception as e:
            log.warning(f"Telegram notify error: {e}")

    async def inject_test_signal(self, symbol: str = "BTCUSDT"):
        """Inject a test signal for verification."""
        test_signal = {
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "strategy": "TestInjection",
            "direction": "Long",
            "price": 85000 if symbol == "BTCUSDT" else 3000,
            "buys_per_sec": 150,
            "delta_pct": 4.0,
            "vol_raise_pct": 300,
            "daily_volume_m": 500,
        }

        self.client = httpx.AsyncClient(timeout=10.0)
        try:
            success = await self._forward_signal(test_signal)
            if success:
                log.info(f"Test signal injected: {symbol}")
                status = await self._get_status()
                if status:
                    log.info(f"AutoTrader status: {json.dumps(status, indent=2)}")
            else:
                log.error("Failed to inject test signal")
        finally:
            await self.client.aclose()


class BinanceRealtimeFeed:
    """
    Real-time price feed from Binance WebSocket.

    Provides accurate prices for entry/exit decisions.
    """

    def __init__(self, symbols: List[str] = None):
        self.symbols = symbols or ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
        self.prices: Dict[str, float] = {}
        self.running = False

    async def start(self):
        """Start WebSocket connection."""
        try:
            import websockets
        except ImportError:
            log.warning("websockets not installed, using HTTP fallback")
            return

        self.running = True
        streams = "/".join([f"{s.lower()}@trade" for s in self.symbols])
        url = f"wss://stream.binance.com:9443/stream?streams={streams}"

        log.info(f"Connecting to Binance WebSocket for {len(self.symbols)} symbols...")

        while self.running:
            try:
                async with websockets.connect(url) as ws:
                    log.info("Binance WebSocket connected")
                    async for msg in ws:
                        if not self.running:
                            break
                        data = json.loads(msg)
                        if "data" in data:
                            trade = data["data"]
                            symbol = trade["s"]
                            price = float(trade["p"])
                            self.prices[symbol] = price
            except Exception as e:
                log.warning(f"WebSocket error: {e}, reconnecting...")
                await asyncio.sleep(5)

    def get_price(self, symbol: str) -> Optional[float]:
        return self.prices.get(symbol)

    async def stop(self):
        self.running = False


# === CLI ===

async def main():
    import argparse

    parser = argparse.ArgumentParser(description="HOPE Signal Watcher")
    parser.add_argument("--watch", action="store_true", help="Watch for signals (default)")
    parser.add_argument("--inject-test", action="store_true", help="Inject test signal")
    parser.add_argument("--symbol", type=str, default="BTCUSDT", help="Symbol for test")
    parser.add_argument("--interval", type=float, default=0.5, help="Poll interval")

    args = parser.parse_args()

    watcher = SignalWatcher(poll_interval=args.interval)

    if args.inject_test:
        await watcher.inject_test_signal(args.symbol)
        return

    # Default: watch mode
    import signal

    def handle_signal(sig, frame):
        log.info("Shutdown requested...")
        watcher.running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    await watcher.start()


if __name__ == "__main__":
    asyncio.run(main())
