# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 20:50:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-29 21:25:00 UTC
# Purpose: Unified Scalping Pipeline - MoonBot → Precursor → Router → AutoTrader
# === END SIGNATURE ===
"""
HOPE AI - Scalping Pipeline Launcher

Запускает полный pipeline для скальпинга:
1. Binance WebSocket Feed (real-time prices + buys_per_sec)
2. MoonBot Live Integration (сигналы → решения)
3. Decision Bridge (решения → AutoTrader)
4. Stats Reporter (периодический отчет)

Usage:
    python scripts/start_scalping_pipeline.py
    python scripts/start_scalping_pipeline.py --dry-run
    python scripts/start_scalping_pipeline.py --no-ws  # без WebSocket
"""

import asyncio
import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from typing import Any, Dict, Optional

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-20s | %(levelname)-5s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ScalpingPipeline")

# Paths
ROOT_DIR = Path(__file__).parent.parent
STATE_DIR = ROOT_DIR / "state" / "ai"
SIGNALS_DIR = STATE_DIR / "signals"
DECISIONS_FILE = STATE_DIR / "decisions.jsonl"
MOONBOT_SIGNALS = SIGNALS_DIR / "moonbot_signals.jsonl"

# Ensure directories
STATE_DIR.mkdir(parents=True, exist_ok=True)
SIGNALS_DIR.mkdir(parents=True, exist_ok=True)


class ScalpingPipeline:
    """
    Unified scalping pipeline controller.

    Components:
    - Binance WebSocket: Real-time prices + buys_per_sec
    - MoonBot Integration: Processes signals through Precursor + ModeRouter
    - Decision Bridge: Forwards BUY decisions to AutoTrader
    - Stats Reporter: Periodic status updates
    """

    def __init__(self, dry_run: bool = False, enable_websocket: bool = True):
        self.dry_run = dry_run
        self.enable_websocket = enable_websocket
        self._running = False
        self._tasks = []

        # Components
        self.ws_feed = None
        self.integration = None

        # Stats
        self.stats = {
            "started_at": None,
            "signals_fed": 0,
            "decisions_made": 0,
            "buys_forwarded": 0,
            "super_scalps_detected": 0,
            "ws_messages": 0,
            "errors": 0,
        }

        logger.info(f"ScalpingPipeline initialized (dry_run={dry_run}, ws={enable_websocket})")

    async def start(self):
        """Start all pipeline components."""
        self._running = True
        self.stats["started_at"] = datetime.now(timezone.utc).isoformat()

        logger.info("=" * 60)
        logger.info("HOPE AI - SCALPING PIPELINE")
        logger.info("=" * 60)

        # Check AutoTrader
        if not self.dry_run:
            if not await self._check_autotrader():
                logger.error("AutoTrader not available! Start it first.")
                return

        # Start components
        try:
            # Import here to avoid circular imports
            from ai_gateway.integrations.moonbot_live import MoonBotLiveIntegration

            self.integration = MoonBotLiveIntegration(
                signals_file=MOONBOT_SIGNALS,
                decisions_file=DECISIONS_FILE,
                enable_event_bus=True,
            )

            # Build task list
            self._tasks = [
                asyncio.create_task(self._run_integration()),
                asyncio.create_task(self._run_decision_bridge()),
                asyncio.create_task(self._run_stats_reporter()),
            ]

            # Start WebSocket feed if enabled
            if self.enable_websocket:
                self._tasks.append(asyncio.create_task(self._run_websocket_feed()))
                logger.info("WebSocket feed: ENABLED")
            else:
                logger.info("WebSocket feed: DISABLED (use --ws to enable)")

            logger.info("Pipeline started. Press Ctrl+C to stop.")
            logger.info(f"Watching: {MOONBOT_SIGNALS}")
            logger.info(f"Decisions: {DECISIONS_FILE}")
            logger.info("-" * 60)

            # Wait for all tasks
            await asyncio.gather(*self._tasks, return_exceptions=True)

        except asyncio.CancelledError:
            logger.info("Pipeline cancelled")
        except Exception as e:
            logger.error(f"Pipeline error: {e}")
            self.stats["errors"] += 1
        finally:
            await self.stop()

    async def stop(self):
        """Stop all components."""
        self._running = False

        # Stop WebSocket feed
        if self.ws_feed is not None:
            await self.ws_feed.stop()

        for task in self._tasks:
            if not task.done():
                task.cancel()

        logger.info("-" * 60)
        logger.info("PIPELINE STOPPED")
        logger.info(f"Stats: {json.dumps(self.stats, indent=2)}")

    async def _check_autotrader(self) -> bool:
        """Check if AutoTrader is running."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get("http://127.0.0.1:8200/status")
                if resp.status_code == 200:
                    data = resp.json()
                    logger.info(f"AutoTrader: {data.get('mode')} (running={data.get('running')})")
                    return True
        except Exception as e:
            logger.warning(f"AutoTrader check failed: {e}")
        return False

    async def _run_integration(self):
        """Run MoonBot integration."""
        logger.info("[Integration] Starting...")
        await self.integration.start()

    async def _run_websocket_feed(self):
        """Run Binance WebSocket for real-time prices and buys_per_sec."""
        logger.info("[WebSocket] Starting...")

        try:
            from ai_gateway.feeds.binance_realtime import BinanceRealtimeFeed, RealtimeData

            def on_realtime_data(data: RealtimeData):
                """Callback for real-time data updates."""
                self.stats["ws_messages"] += 1

                # Detect SUPER_SCALP conditions (buys_per_sec >= 100)
                if data.is_super_scalp_ready():
                    self.stats["super_scalps_detected"] += 1
                    logger.warning(
                        f"🚀 SUPER_SCALP DETECTED: {data.symbol} | "
                        f"Buys/sec: {data.buys_per_sec:.1f} | "
                        f"Price: ${data.price:.4f}"
                    )

                    # Inject signal to MoonBot integration if running
                    if self.integration and hasattr(self.integration, 'inject_super_scalp'):
                        self.integration.inject_super_scalp(data.symbol, data)

            # Create feed
            self.ws_feed = BinanceRealtimeFeed(on_data=on_realtime_data)

            # Subscribe to symbols from existing decisions
            symbols_to_track = await self._get_tracked_symbols()
            if symbols_to_track:
                await self.ws_feed.subscribe(symbols_to_track)
                logger.info(f"[WebSocket] Subscribed to {len(symbols_to_track)} symbols")
            else:
                # Default symbols for monitoring
                default_symbols = ["BTCUSDT", "ETHUSDT", "XVSUSDT", "SYNUSDT", "ARPAUSDT"]
                await self.ws_feed.subscribe(default_symbols)
                logger.info(f"[WebSocket] Subscribed to default symbols")

            # Run feed
            await self.ws_feed.run()

        except ImportError as e:
            logger.error(f"[WebSocket] Import error: {e}")
            logger.info("[WebSocket] Install websockets: pip install websockets")
        except Exception as e:
            logger.error(f"[WebSocket] Error: {e}")
            self.stats["errors"] += 1

    async def _get_tracked_symbols(self) -> list:
        """Get symbols from recent decisions for WebSocket tracking."""
        symbols = set()

        # From decisions file
        if DECISIONS_FILE.exists():
            try:
                with open(DECISIONS_FILE, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.strip():
                            try:
                                d = json.loads(line)
                                sym = d.get("symbol", "")
                                if sym:
                                    symbols.add(sym.upper())
                            except json.JSONDecodeError:
                                pass
            except Exception:
                pass

        # From signals file
        if MOONBOT_SIGNALS.exists():
            try:
                with open(MOONBOT_SIGNALS, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.strip():
                            try:
                                s = json.loads(line)
                                sym = s.get("symbol", "")
                                if sym:
                                    symbols.add(sym.upper())
                            except json.JSONDecodeError:
                                pass
            except Exception:
                pass

        return list(symbols)[:50]  # Limit to 50 symbols

    async def _run_decision_bridge(self):
        """Run decision bridge (forwards BUY to AutoTrader)."""
        logger.info("[Bridge] Starting...")

        import httpx

        last_count = 0
        processed_ids = set()

        # Load existing
        if DECISIONS_FILE.exists():
            with open(DECISIONS_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        d = json.loads(line)
                        processed_ids.add(d.get("signal_id", ""))
            last_count = len(processed_ids)
            logger.info(f"[Bridge] Loaded {last_count} existing decisions")

        async with httpx.AsyncClient(timeout=5) as client:
            while self._running:
                try:
                    if DECISIONS_FILE.exists():
                        with open(DECISIONS_FILE, 'r', encoding='utf-8') as f:
                            lines = f.readlines()

                        if len(lines) > last_count:
                            for line in lines[last_count:]:
                                if not line.strip():
                                    continue

                                try:
                                    decision = json.loads(line)
                                except:
                                    continue

                                signal_id = decision.get("signal_id", "")
                                if signal_id in processed_ids:
                                    continue

                                processed_ids.add(signal_id)
                                self.stats["decisions_made"] += 1

                                # Forward BUY decisions
                                if decision.get("final_action") == "BUY":
                                    if self.dry_run:
                                        logger.info(f"[DRY-RUN] Would forward BUY: {decision.get('symbol')}")
                                    else:
                                        await self._forward_buy(client, decision)

                            last_count = len(lines)

                except Exception as e:
                    logger.error(f"[Bridge] Error: {e}")
                    self.stats["errors"] += 1

                await asyncio.sleep(0.5)

    async def _forward_buy(self, client, decision: dict):
        """Forward BUY decision to AutoTrader."""
        symbol = decision.get("symbol", "")
        mode_info = decision.get("mode", {})
        mode_name = mode_info.get("name", "unknown")
        config = mode_info.get("config", {}) or {}

        signal = {
            "symbol": symbol,
            "strategy": f"Scalp_{mode_name}",
            "direction": "Long",
            "price": 0,
            "buys_per_sec": 50 if mode_name == "super_scalp" else 35,
            "delta_pct": config.get("target_pct", 1.0),
            "vol_raise_pct": 100,
        }

        try:
            resp = await client.post("http://127.0.0.1:8200/signal", json=signal)
            if resp.status_code == 200:
                logger.info(f"[Bridge] Forwarded BUY {symbol} ({mode_name}) -> AutoTrader")
                self.stats["buys_forwarded"] += 1
            else:
                logger.warning(f"[Bridge] AutoTrader rejected: {resp.text}")
        except Exception as e:
            logger.error(f"[Bridge] Forward failed: {e}")

    async def _run_stats_reporter(self):
        """Periodic stats reporter."""
        while self._running:
            await asyncio.sleep(60)  # Every minute

            integration_stats = self.integration.get_stats() if self.integration else {}
            ws_stats = self.ws_feed.get_stats() if self.ws_feed else {}

            logger.info(
                f"[Stats] Signals: {integration_stats.get('signals_processed', 0)} | "
                f"Precursors: {integration_stats.get('precursors_detected', 0)} | "
                f"BUYs: {self.stats['buys_forwarded']} | "
                f"SuperScalps: {self.stats['super_scalps_detected']}"
            )

            if ws_stats:
                logger.info(
                    f"[WebSocket] Connected: {ws_stats.get('is_connected')} | "
                    f"Symbols: {ws_stats.get('symbols_count', 0)} | "
                    f"Messages: {self.stats['ws_messages']}"
                )


async def feed_existing_signals():
    """Feed existing MoonBot signals into the pipeline for testing."""
    logger.info("Feeding existing MoonBot signals...")

    signal_files = list(Path("data/moonbot_signals").glob("*.jsonl"))
    if not signal_files:
        logger.warning("No signal files found in data/moonbot_signals/")
        return

    all_signals = []
    for f in signal_files:
        with open(f, 'r', encoding='utf-8') as fh:
            for line in fh:
                if line.strip():
                    try:
                        all_signals.append(json.loads(line))
                    except:
                        pass

    logger.info(f"Loaded {len(all_signals)} signals from {len(signal_files)} files")

    # Write to pipeline input
    MOONBOT_SIGNALS.parent.mkdir(parents=True, exist_ok=True)

    with open(MOONBOT_SIGNALS, 'a', encoding='utf-8') as f:
        for sig in all_signals[-50:]:  # Last 50 signals
            f.write(json.dumps(sig) + "\n")

    logger.info(f"Fed {min(50, len(all_signals))} signals to pipeline")


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="HOPE AI Scalping Pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Don't forward to AutoTrader")
    parser.add_argument("--feed", action="store_true", help="Feed existing signals first")
    parser.add_argument("--no-ws", action="store_true", help="Disable WebSocket feed")
    args = parser.parse_args()

    if args.feed:
        await feed_existing_signals()

    pipeline = ScalpingPipeline(
        dry_run=args.dry_run,
        enable_websocket=not args.no_ws
    )

    # Handle shutdown
    loop = asyncio.get_event_loop()

    def shutdown_handler():
        logger.info("Shutdown signal received...")
        asyncio.create_task(pipeline.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown_handler)
        except NotImplementedError:
            pass  # Windows

    try:
        await pipeline.start()
    except KeyboardInterrupt:
        await pipeline.stop()


if __name__ == "__main__":
    asyncio.run(main())
