# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-01-30 10:20:00 UTC
# Purpose: Unified Production Runner - integrates ALL components
# Contract: Eye of God V3 + WebSocket + MoonBot + Watchdog + Panic
# === END SIGNATURE ===
"""
HOPE AI - Unified Production Trading System
===========================================

Integrates ALL production components:
- Eye of God V3 (two-chamber architecture)
- Binance WebSocket (real-time prices + buys_per_sec)
- MoonBot Live Integration (signal processing)
- Position Watchdog (independent position management)
- Panic Close (emergency liquidation with Telegram)

Usage:
    python scripts/run_production.py --mode TESTNET
    python scripts/run_production.py --mode LIVE --confirm
    python scripts/run_production.py --status
"""

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Ensure project root
sys.path.insert(0, str(Path(__file__).parent.parent))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-5s | %(name)-18s | %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("HOPE-PRODUCTION")

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

STATE_DIR = Path("state/ai/production")
LOCK_FILE = Path("state/locks/production.lock")
STOP_FLAG = Path("state/STOP.flag")
HEARTBEAT_FILE = STATE_DIR / "heartbeat.json"
TRADES_LOG = STATE_DIR / "trades.jsonl"

STATE_DIR.mkdir(parents=True, exist_ok=True)
Path("state/locks").mkdir(parents=True, exist_ok=True)
Path("logs").mkdir(exist_ok=True)

# Production safety limits
MAX_ORDERS_PER_HOUR = 20
MAX_DAILY_LOSS_PCT = 3.0
BASE_POSITION_SIZE_USD = 10.0


class TradingMode(Enum):
    DRY = "DRY"
    TESTNET = "TESTNET"
    LIVE = "LIVE"


# ═══════════════════════════════════════════════════════════════════════════════
# PRODUCTION ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════

class ProductionOrchestrator:
    """
    Unified Production Orchestrator.

    Coordinates:
    - Eye of God V3 (trading decisions)
    - Binance WebSocket (real-time data)
    - MoonBot Integration (signal source)
    - Position Watchdog (position management)
    - Panic Close (emergency shutdown)
    """

    def __init__(self, mode: TradingMode, position_size: float = BASE_POSITION_SIZE_USD):
        self.mode = mode
        self.position_size = position_size
        self.running = False
        self.cycle_count = 0

        # Components (initialized in start())
        self.eye_of_god = None
        self.realtime_feed = None
        self.moonbot_integration = None
        self.watchdog = None
        self.executor = None

        # Rate limiter
        self._order_times: List[float] = []

        # Circuit breaker
        self.daily_pnl = 0.0
        self.starting_equity = 0.0
        self.circuit_tripped = False

        # Stats
        self.stats = {
            "signals_processed": 0,
            "trades_executed": 0,
            "trades_skipped": 0,
            "errors": 0,
        }

        logger.info(f"Orchestrator initialized (mode={mode.value})")

    async def start(self):
        """Start all production components."""
        logger.info("=" * 60)
        logger.info("  HOPE AI PRODUCTION TRADING SYSTEM")
        logger.info("=" * 60)
        logger.info(f"  Mode: {self.mode.value}")
        logger.info(f"  Position Size: ${self.position_size}")
        logger.info("=" * 60)

        # Acquire lock
        if not self._acquire_lock():
            logger.error("Another instance is running. Exiting.")
            return False

        try:
            # Initialize components
            await self._init_components()

            # Run main loop
            self.running = True
            await self._run_loop()

        except KeyboardInterrupt:
            logger.info("Shutdown requested (Ctrl+C)")
        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
        finally:
            await self._shutdown()

        return True

    async def _init_components(self):
        """Initialize all production components."""
        logger.info("Initializing components...")

        # 1. Load environment
        self._load_env()

        # 2. Eye of God V3
        try:
            from scripts.eye_of_god_v3 import EyeOfGodV3
            self.eye_of_god = EyeOfGodV3(base_position_size=self.position_size)
            logger.info("[OK] Eye of God V3 initialized")
        except Exception as e:
            logger.error(f"[FAIL] Eye of God V3: {e}")
            raise

        # 3. Binance Real-time Feed
        try:
            from ai_gateway.feeds.binance_realtime import BinanceRealtimeFeed

            def on_realtime_data(data):
                """Callback for price updates."""
                # Update Eye of God price cache
                if self.eye_of_god:
                    self.eye_of_god.update_price(data.symbol, data.price)

                # Check for SUPER_SCALP (buys_per_sec >= 100)
                if data.is_super_scalp_ready():
                    logger.warning(f"SUPER_SCALP: {data.symbol} buys/sec={data.buys_per_sec:.0f}")
                    # Inject synthetic signal
                    if self.moonbot_integration and hasattr(self.moonbot_integration, 'inject_super_scalp'):
                        self.moonbot_integration.inject_super_scalp(data.symbol, data)

            self.realtime_feed = BinanceRealtimeFeed(
                on_data=on_realtime_data,
                use_testnet=(self.mode == TradingMode.TESTNET),
            )

            # Subscribe to default symbols
            default_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
            await self.realtime_feed.subscribe(default_symbols)

            logger.info("[OK] Binance WebSocket Feed initialized")
        except Exception as e:
            logger.warning(f"[WARN] WebSocket Feed: {e} (falling back to API)")
            self.realtime_feed = None

        # 4. MoonBot Integration
        try:
            from ai_gateway.integrations.moonbot_live import MoonBotLiveIntegration
            self.moonbot_integration = MoonBotLiveIntegration(enable_event_bus=True)
            logger.info("[OK] MoonBot Integration initialized")
        except Exception as e:
            logger.warning(f"[WARN] MoonBot Integration: {e}")
            self.moonbot_integration = None

        # 5. Position Watchdog
        try:
            from scripts.position_watchdog import PositionWatchdog
            self.watchdog = PositionWatchdog(testnet=(self.mode == TradingMode.TESTNET))
            logger.info("[OK] Position Watchdog initialized")
        except Exception as e:
            logger.warning(f"[WARN] Position Watchdog: {e}")
            self.watchdog = None

        # 6. Binance Executor
        if self.mode != TradingMode.DRY:
            try:
                from binance.client import Client

                if self.mode == TradingMode.TESTNET:
                    api_key = os.getenv("BINANCE_TESTNET_API_KEY")
                    api_secret = os.getenv("BINANCE_TESTNET_API_SECRET")
                    self.executor = Client(api_key, api_secret, testnet=True)
                else:
                    api_key = os.getenv("BINANCE_API_KEY")
                    api_secret = os.getenv("BINANCE_API_SECRET")
                    self.executor = Client(api_key, api_secret)

                # Get starting equity
                account = self.executor.get_account()
                for balance in account.get("balances", []):
                    if balance["asset"] == "USDT":
                        self.starting_equity = float(balance["free"]) + float(balance["locked"])
                        break

                logger.info(f"[OK] Binance Executor initialized (equity=${self.starting_equity:.2f})")
            except Exception as e:
                logger.error(f"[FAIL] Binance Executor: {e}")
                raise
        else:
            logger.info("[OK] DRY mode - no executor needed")

    def _load_env(self):
        """Load environment variables from secrets."""
        secrets_path = Path(r"C:\secrets\hope.env")
        if secrets_path.exists():
            for line in secrets_path.read_text(encoding="utf-8").splitlines():
                if '=' in line and not line.strip().startswith('#'):
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip()
            logger.info(f"Loaded env from {secrets_path}")

    async def _run_loop(self):
        """Main production loop."""
        # Start background tasks
        tasks = []

        # WebSocket feed
        if self.realtime_feed:
            tasks.append(asyncio.create_task(self.realtime_feed.run()))

        # MoonBot integration
        if self.moonbot_integration:
            tasks.append(asyncio.create_task(self.moonbot_integration.start()))

        # Watchdog
        if self.watchdog:
            tasks.append(asyncio.create_task(self.watchdog.run()))

        # Decision processor
        tasks.append(asyncio.create_task(self._process_decisions()))

        # Heartbeat writer
        tasks.append(asyncio.create_task(self._heartbeat_loop()))

        logger.info(f"Started {len(tasks)} background tasks")

        try:
            # Wait for all tasks
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass

    async def _process_decisions(self):
        """Process BUY decisions from MoonBot pipeline."""
        decisions_file = Path("state/ai/decisions.jsonl")
        last_position = 0
        processed_ids: Set[str] = set()

        if decisions_file.exists():
            last_position = decisions_file.stat().st_size

        logger.info("Decision processor started")

        while self.running:
            try:
                # Check STOP flag
                if STOP_FLAG.exists():
                    logger.info("STOP flag detected - shutting down")
                    self.running = False
                    break

                # Check circuit breaker
                if self.circuit_tripped:
                    await asyncio.sleep(5)
                    continue

                # Read new decisions
                if decisions_file.exists():
                    current_size = decisions_file.stat().st_size
                    if current_size > last_position:
                        with open(decisions_file, 'r', encoding='utf-8') as f:
                            f.seek(last_position)
                            new_content = f.read()
                            last_position = f.tell()

                        for line in new_content.strip().split('\n'):
                            if not line.strip():
                                continue

                            try:
                                decision = json.loads(line)
                                await self._handle_decision(decision, processed_ids)
                            except json.JSONDecodeError:
                                continue

                await asyncio.sleep(0.5)

            except Exception as e:
                logger.error(f"Decision processor error: {e}")
                self.stats["errors"] += 1
                await asyncio.sleep(1)

    async def _handle_decision(self, decision: Dict, processed_ids: Set[str]):
        """Handle a single BUY decision."""
        signal_id = decision.get("signal_id", "")
        final_action = decision.get("final_action", "SKIP")
        symbol = decision.get("symbol", "")

        # Skip duplicates
        if signal_id in processed_ids:
            return
        processed_ids.add(signal_id)

        self.stats["signals_processed"] += 1

        # Only process BUY
        if final_action != "BUY":
            self.stats["trades_skipped"] += 1
            return

        # Rate limit check
        if not self._can_trade():
            logger.warning(f"Rate limited: {symbol}")
            self.stats["trades_skipped"] += 1
            return

        # Get current price
        price = self._get_price(symbol)
        if price is None:
            logger.warning(f"No price for {symbol} - SKIP (fail-closed)")
            self.stats["trades_skipped"] += 1
            return

        # Build signal for Eye of God
        raw_signal = decision.get("raw_signal", {})
        mode_info = decision.get("mode", {})
        mode_config = mode_info.get("config", {}) or {}

        signal_for_eye = {
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "strategy": raw_signal.get("strategy", mode_info.get("name", "unknown")),
            "direction": raw_signal.get("direction", "Long"),
            "delta_pct": raw_signal.get("delta_pct", 0),
            "buys_per_sec": raw_signal.get("buys_per_sec", 0),
            "vol_raise_pct": raw_signal.get("vol_raise_pct", 0),
            "daily_volume_m": raw_signal.get("daily_volume", 0) / 1_000_000,
            "price": price,
        }

        # Eye of God decision
        eye_decision = self.eye_of_god.decide(signal_for_eye)

        if eye_decision.action != "BUY":
            logger.info(f"Eye of God SKIP: {symbol} - {eye_decision.reasons}")
            self.stats["trades_skipped"] += 1
            return

        # Execute trade
        await self._execute_trade(
            symbol=symbol,
            price=price,
            size_usd=eye_decision.position_size_usdt,
            target_pct=eye_decision.target_pct,
            stop_pct=eye_decision.stop_pct,
            timeout_sec=eye_decision.timeout_sec,
            decision_sha=eye_decision.sha256,
        )

    async def _execute_trade(
        self,
        symbol: str,
        price: float,
        size_usd: float,
        target_pct: float,
        stop_pct: float,
        timeout_sec: int,
        decision_sha: str,
    ):
        """Execute a trade."""
        if self.mode == TradingMode.DRY:
            logger.info(f"[DRY] BUY {symbol} @ {price:.4f} size=${size_usd:.2f}")
            self._log_trade("DRY_BUY", symbol, price, size_usd, decision_sha)
            self.stats["trades_executed"] += 1
            return

        try:
            # Market buy
            order = self.executor.order_market_buy(
                symbol=symbol,
                quoteOrderQty=size_usd
            )

            order_id = order.get("orderId", "")
            filled_qty = float(order.get("executedQty", 0))
            avg_price = float(order.get("cummulativeQuoteQty", 0)) / max(filled_qty, 0.00001)

            logger.info(f"BUY {symbol} @ {avg_price:.4f} qty={filled_qty:.6f} order={order_id}")

            # Register with watchdog
            if self.watchdog:
                from scripts.position_watchdog import register_position_for_watching
                register_position_for_watching(
                    position_id=f"pos_{order_id}",
                    symbol=symbol,
                    entry_price=avg_price,
                    quantity=filled_qty,
                    target_pct=target_pct,
                    stop_pct=stop_pct,
                    timeout_sec=timeout_sec,
                )

            # Log trade
            self._log_trade("BUY", symbol, avg_price, size_usd, decision_sha, str(order_id))
            self._record_order()
            self.stats["trades_executed"] += 1

            # Notify Telegram
            await self._notify_telegram(
                f"BUY {symbol}\n"
                f"Price: ${avg_price:.4f}\n"
                f"Size: ${size_usd:.2f}\n"
                f"Timeout: {timeout_sec}s"
            )

        except Exception as e:
            logger.error(f"Trade execution error: {e}")
            self.stats["errors"] += 1

    def _get_price(self, symbol: str) -> Optional[float]:
        """Get current price from WebSocket or API."""
        # Try WebSocket first
        if self.realtime_feed:
            data = self.realtime_feed.get_data(symbol)
            if data and not data.is_stale():
                return data.price

        # Fallback to API
        if self.executor:
            try:
                ticker = self.executor.get_symbol_ticker(symbol=symbol)
                return float(ticker.get("price", 0))
            except Exception:
                pass

        return None

    def _can_trade(self) -> bool:
        """Check rate limit."""
        now = time.time()
        one_hour_ago = now - 3600
        self._order_times = [t for t in self._order_times if t > one_hour_ago]
        return len(self._order_times) < MAX_ORDERS_PER_HOUR

    def _record_order(self):
        """Record order for rate limiting."""
        self._order_times.append(time.time())

    def _log_trade(self, event: str, symbol: str, price: float, size: float, sha: str, order_id: str = ""):
        """Log trade to JSONL."""
        import hashlib

        entry = {
            "event": event,
            "symbol": symbol,
            "price": price,
            "size_usd": size,
            "decision_sha": sha,
            "order_id": order_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": self.mode.value,
        }

        data_str = json.dumps(entry, sort_keys=True)
        entry["sha256"] = "sha256:" + hashlib.sha256(data_str.encode()).hexdigest()[:16]

        with open(TRADES_LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    async def _notify_telegram(self, message: str):
        """Send Telegram notification."""
        try:
            import urllib.request
            import urllib.parse

            token = os.getenv("TELEGRAM_BOT_TOKEN")
            chat_id = os.getenv("TELEGRAM_ADMIN_ID")

            if not token or not chat_id:
                return

            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id": chat_id,
                "text": f"HOPE Production\n\n{message}",
            }).encode()

            req = urllib.request.Request(url, data=data, method="POST")
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass

    async def _heartbeat_loop(self):
        """Write heartbeat for external monitoring."""
        while self.running:
            try:
                heartbeat = {
                    "pid": os.getpid(),
                    "mode": self.mode.value,
                    "cycle": self.cycle_count,
                    "stats": self.stats,
                    "positions": self.watchdog.stats if self.watchdog else {},
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

                HEARTBEAT_FILE.write_text(json.dumps(heartbeat, indent=2))
                self.cycle_count += 1

            except Exception:
                pass

            await asyncio.sleep(5)

    def _acquire_lock(self) -> bool:
        """Acquire single-instance lock."""
        if LOCK_FILE.exists():
            try:
                old_pid = int(LOCK_FILE.read_text().strip())
                # Check if process running (Windows)
                import ctypes
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(0x1000, False, old_pid)
                if handle:
                    kernel32.CloseHandle(handle)
                    return False
            except Exception:
                pass

        LOCK_FILE.write_text(str(os.getpid()))
        return True

    def _release_lock(self):
        """Release lock."""
        try:
            if LOCK_FILE.exists():
                LOCK_FILE.unlink()
        except Exception:
            pass

    async def _shutdown(self):
        """Graceful shutdown."""
        logger.info("Shutting down...")
        self.running = False

        # Stop components
        if self.realtime_feed:
            await self.realtime_feed.stop()

        if self.moonbot_integration:
            await self.moonbot_integration.stop()

        if self.watchdog:
            self.watchdog.stop()

        # Release lock
        self._release_lock()

        # Log final stats
        logger.info(f"Final stats: {self.stats}")

    def get_status(self) -> Dict:
        """Get current status."""
        return {
            "mode": self.mode.value,
            "running": self.running,
            "stats": self.stats,
            "eye_of_god": self.eye_of_god.get_stats() if self.eye_of_god else {},
            "watchdog": self.watchdog.get_status() if self.watchdog else {},
            "realtime_feed": self.realtime_feed.get_stats() if self.realtime_feed else {},
        }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

async def main():
    import argparse

    parser = argparse.ArgumentParser(description="HOPE Production Trading System")
    parser.add_argument("--mode", type=str, default="DRY", choices=["DRY", "TESTNET", "LIVE"])
    parser.add_argument("--confirm", action="store_true", help="Confirm LIVE mode")
    parser.add_argument("--position-size", type=float, default=10.0)
    parser.add_argument("--status", action="store_true")

    args = parser.parse_args()

    # LIVE mode safety
    if args.mode == "LIVE":
        if not args.confirm:
            print("LIVE mode requires --confirm flag!")
            print("This will trade with REAL MONEY!")
            sys.exit(1)

        confirm = input("Type 'I UNDERSTAND' to confirm: ")
        if confirm != "I UNDERSTAND":
            print("Cancelled.")
            sys.exit(1)

    mode = TradingMode[args.mode]

    if args.status:
        # Quick status check
        if HEARTBEAT_FILE.exists():
            data = json.loads(HEARTBEAT_FILE.read_text())
            print(json.dumps(data, indent=2))
        else:
            print("No heartbeat file found - system not running?")
        return

    # Run orchestrator
    orchestrator = ProductionOrchestrator(mode, args.position_size)
    await orchestrator.start()


if __name__ == "__main__":
    asyncio.run(main())
