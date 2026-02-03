# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-02-03T20:25:00Z
# Purpose: Guardian - Independent safety watchdog with OWN Binance client
# Contract: FAIL-CLOSED, panic close, circuit breaker
# === END SIGNATURE ===
#
# === DEPENDENCIES ===
# READS FROM: state/positions/active.json, state/events/journal_*.jsonl
# WRITES TO: state/events/journal_*.jsonl, state/guardian/state.json
# CALLS: Binance API (INDEPENDENT client), Trading Core heartbeat
# NEXT IN CHAIN: scripts/interface.py (via Event Transport)
# === END DEPENDENCIES ===
"""
GUARDIAN - The Protector of HOPE

WHAT THIS IS:
    Independent safety watchdog that:
    - Monitors positions with OWN Binance client
    - Enforces stop-loss if Trading Core fails
    - Triggers circuit breaker on daily loss limit
    - PANIC close all positions if Core dies

WHY SEPARATE PROCESS:
    If Trading Core crashes, Guardian can still:
    - See open positions
    - Close them safely
    - Alert via Telegram

CRITICAL INVARIANT:
    Guardian has its OWN Binance API client.
    If Core dies, Guardian still works.

HEARTBEAT CHAIN:
    Trading Core → Guardian (every 5s)
    If no heartbeat for 30s → PANIC

ARCHITECTURE:
    +------------------+
    |     GUARDIAN     |
    |                  |
    |  Position Monitor|
    |        ↓         |
    |  Stop-Loss Check |
    |        ↓         |
    |  Circuit Breaker |
    |        ↓         |
    |  Panic Handler   |
    +------------------+
           ↑
    Heartbeat from Core

USAGE:
    python -m scripts.guardian --mode TESTNET
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import signal
import argparse
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
from enum import Enum

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-5s | GUARDIAN | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("GUARDIAN")


# === Mode ===

class GuardianMode(Enum):
    DRY = "DRY"
    TESTNET = "TESTNET"
    LIVE = "LIVE"


# === Position State ===

@dataclass
class TrackedPosition:
    """Position being tracked by Guardian."""
    position_id: str
    symbol: str
    side: str
    entry_price: float
    quantity: float
    opened_at: str
    correlation_id: str
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    current_price: Optional[float] = None
    unrealized_pnl_pct: float = 0.0
    last_check: Optional[str] = None


# === Binance Client (INDEPENDENT) ===

class GuardianBinanceClient:
    """
    Guardian's OWN Binance client.

    CRITICAL: This is SEPARATE from Trading Core's client.
    If Core dies, Guardian can still close positions.
    """

    def __init__(self, mode: GuardianMode):
        self.mode = mode
        self._client = None
        self._prices: Dict[str, float] = {}

    async def start(self):
        """Initialize Binance client."""
        if self.mode == GuardianMode.DRY:
            log.info("GuardianBinanceClient: DRY mode")
            return

        try:
            from binance.client import Client

            api_key = os.getenv("BINANCE_API_KEY")
            api_secret = os.getenv("BINANCE_API_SECRET")

            if not api_key or not api_secret:
                log.error("Missing Binance credentials for Guardian")
                return

            testnet = self.mode == GuardianMode.TESTNET
            self._client = Client(api_key, api_secret, testnet=testnet)

            log.info(f"GuardianBinanceClient: Connected ({'testnet' if testnet else 'LIVE'})")

        except ImportError:
            log.warning("python-binance not installed")
        except Exception as e:
            log.error(f"Guardian Binance init failed: {e}")

    async def get_price(self, symbol: str) -> Optional[float]:
        """Get current price from Binance."""
        if self.mode == GuardianMode.DRY:
            # Return cached or mock price
            return self._prices.get(symbol, 50000.0)

        if not self._client:
            return None

        try:
            ticker = self._client.get_symbol_ticker(symbol=symbol)
            price = float(ticker.get("price", 0))
            self._prices[symbol] = price
            return price
        except Exception as e:
            log.error(f"Failed to get price for {symbol}: {e}")
            return self._prices.get(symbol)

    async def close_position(self, position: TrackedPosition) -> bool:
        """
        PANIC close a position.

        This is Guardian's nuclear option.
        """
        log.warning(f"GUARDIAN CLOSING POSITION: {position.position_id} {position.symbol}")

        if self.mode == GuardianMode.DRY:
            log.info(f"[DRY] Would close {position.quantity} {position.symbol}")
            return True

        if not self._client:
            log.error("Cannot close - no Binance client")
            return False

        try:
            from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET

            # Opposite side to close
            close_side = SIDE_SELL if position.side == "BUY" else SIDE_BUY

            result = self._client.create_order(
                symbol=position.symbol,
                side=close_side,
                type=ORDER_TYPE_MARKET,
                quantity=position.quantity,
            )

            log.info(f"Position closed: {result.get('orderId')}")
            return True

        except Exception as e:
            log.error(f"FAILED TO CLOSE POSITION: {e}")
            return False

    async def close_all_positions(self, positions: List[TrackedPosition]) -> int:
        """
        PANIC: Close ALL positions.

        Returns number of successfully closed positions.
        """
        log.warning(f"GUARDIAN PANIC: Closing {len(positions)} positions")

        closed = 0
        for pos in positions:
            if await self.close_position(pos):
                closed += 1

        log.warning(f"PANIC CLOSE: {closed}/{len(positions)} positions closed")
        return closed


# === Heartbeat Monitor ===

class HeartbeatMonitor:
    """
    Monitor heartbeats from Trading Core.

    If no heartbeat for TIMEOUT_SEC → PANIC
    """

    TIMEOUT_SEC = 30.0  # Core must heartbeat every 30s

    def __init__(self):
        self._last_heartbeat: float = time.time()
        self._core_alive = True

    def record_heartbeat(self):
        """Record heartbeat from Core."""
        self._last_heartbeat = time.time()
        self._core_alive = True

    def is_core_alive(self) -> bool:
        """Check if Core is alive."""
        elapsed = time.time() - self._last_heartbeat
        self._core_alive = elapsed < self.TIMEOUT_SEC
        return self._core_alive

    def get_status(self) -> Dict[str, Any]:
        """Get heartbeat status."""
        return {
            "core_alive": self._core_alive,
            "last_heartbeat": self._last_heartbeat,
            "elapsed_sec": time.time() - self._last_heartbeat,
        }


# === Circuit Breaker ===

class CircuitBreaker:
    """
    Daily loss limit circuit breaker.

    If daily loss exceeds LIMIT → STOP all trading
    """

    DAILY_LOSS_LIMIT_PCT = 3.0  # 3% max daily loss

    def __init__(self):
        self._daily_pnl: float = 0.0
        self._tripped = False
        self._trip_reason: str = ""

    def record_pnl(self, pnl_pct: float):
        """Record PnL from closed position."""
        self._daily_pnl += pnl_pct

        if self._daily_pnl < -self.DAILY_LOSS_LIMIT_PCT:
            self._tripped = True
            self._trip_reason = f"Daily loss {self._daily_pnl:.2f}% exceeds limit {self.DAILY_LOSS_LIMIT_PCT}%"
            log.error(f"CIRCUIT BREAKER TRIPPED: {self._trip_reason}")

    def is_tripped(self) -> bool:
        """Check if circuit breaker is tripped."""
        return self._tripped

    def reset(self):
        """Reset for new day."""
        self._daily_pnl = 0.0
        self._tripped = False
        self._trip_reason = ""

    def get_status(self) -> Dict[str, Any]:
        """Get circuit breaker status."""
        return {
            "tripped": self._tripped,
            "daily_pnl_pct": self._daily_pnl,
            "limit_pct": self.DAILY_LOSS_LIMIT_PCT,
            "trip_reason": self._trip_reason,
        }


# === Position Monitor ===

class PositionMonitor:
    """
    Monitor open positions for stop-loss violations.
    """

    def __init__(self, binance: GuardianBinanceClient):
        self.binance = binance
        self._positions: Dict[str, TrackedPosition] = {}
        self._positions_file = PROJECT_ROOT / "state" / "positions" / "active.json"

    def load_positions(self) -> List[TrackedPosition]:
        """Load positions from Trading Core's state file."""
        if not self._positions_file.exists():
            return []

        try:
            data = json.loads(self._positions_file.read_text(encoding="utf-8"))
            positions = []
            for pos_data in data.get("positions", []):
                pos = TrackedPosition(**pos_data)
                positions.append(pos)
                self._positions[pos.position_id] = pos
            return positions
        except Exception as e:
            log.error(f"Failed to load positions: {e}")
            return []

    async def check_position(self, position: TrackedPosition) -> Dict[str, Any]:
        """
        Check position against stop-loss and take-profit.

        Returns:
            {"action": "HOLD" | "CLOSE_SL" | "CLOSE_TP", ...}
        """
        # Get current price
        current_price = await self.binance.get_price(position.symbol)
        if current_price is None:
            return {"action": "HOLD", "reason": "NO_PRICE"}

        position.current_price = current_price
        position.last_check = datetime.now(timezone.utc).isoformat()

        # Calculate unrealized PnL
        pnl_pct = ((current_price - position.entry_price) / position.entry_price) * 100
        if position.side == "SELL":
            pnl_pct = -pnl_pct
        position.unrealized_pnl_pct = pnl_pct

        # Check stop-loss
        if position.stop_loss and pnl_pct <= position.stop_loss:
            return {
                "action": "CLOSE_SL",
                "reason": f"Stop-loss hit: {pnl_pct:.2f}% <= {position.stop_loss}%",
                "pnl_pct": pnl_pct,
            }

        # Check take-profit
        if position.take_profit and pnl_pct >= position.take_profit:
            return {
                "action": "CLOSE_TP",
                "reason": f"Take-profit hit: {pnl_pct:.2f}% >= {position.take_profit}%",
                "pnl_pct": pnl_pct,
            }

        return {"action": "HOLD", "pnl_pct": pnl_pct}

    def get_all_positions(self) -> List[TrackedPosition]:
        """Get all tracked positions."""
        return list(self._positions.values())


# === Guardian Core ===

class Guardian:
    """
    The Protector - Independent safety watchdog.

    CRITICAL: Has its OWN Binance client.
    """

    def __init__(self, mode: GuardianMode = GuardianMode.DRY):
        self.mode = mode
        self.binance = GuardianBinanceClient(mode)
        self.heartbeat_monitor = HeartbeatMonitor()
        self.circuit_breaker = CircuitBreaker()
        self.position_monitor = PositionMonitor(self.binance)

        self._running = False
        self._check_interval = 1.0  # Check every 1 second

        # State file
        self._state_file = PROJECT_ROOT / "state" / "guardian" / "state.json"
        self._state_file.parent.mkdir(parents=True, exist_ok=True)

    async def start(self):
        """Start Guardian."""
        log.info("=" * 60)
        log.info(f"  GUARDIAN - STARTING")
        log.info(f"  Mode: {self.mode.value}")
        log.info("=" * 60)

        self._running = True

        # Initialize Binance client
        await self.binance.start()

        # Start monitoring loops
        asyncio.create_task(self._heartbeat_loop())
        asyncio.create_task(self._position_loop())
        asyncio.create_task(self._transport_loop())

        log.info("Guardian started")

    async def _heartbeat_loop(self):
        """Monitor heartbeats from Trading Core."""
        while self._running:
            if not self.heartbeat_monitor.is_core_alive():
                log.error("CORE HEARTBEAT TIMEOUT - Initiating PANIC close")
                await self._panic_close("Core heartbeat timeout")

            await asyncio.sleep(5)

    async def _position_loop(self):
        """Monitor positions for stop-loss/take-profit."""
        while self._running:
            try:
                positions = self.position_monitor.load_positions()

                for pos in positions:
                    result = await self.position_monitor.check_position(pos)

                    if result["action"] in ("CLOSE_SL", "CLOSE_TP"):
                        log.warning(f"Position {pos.position_id} needs close: {result['reason']}")

                        if await self.binance.close_position(pos):
                            self.circuit_breaker.record_pnl(result.get("pnl_pct", 0))

                            # Publish close event
                            try:
                                from core.events.transport import publish_event
                                publish_event(
                                    "CLOSE",
                                    {
                                        "position_id": pos.position_id,
                                        "symbol": pos.symbol,
                                        "reason": result["reason"],
                                        "pnl_pct": result.get("pnl_pct", 0),
                                    },
                                    pos.correlation_id,
                                    source="guardian",
                                )
                            except Exception as e:
                                log.error(f"Failed to publish CLOSE event: {e}")

            except Exception as e:
                log.error(f"Position check error: {e}")

            # Check circuit breaker
            if self.circuit_breaker.is_tripped():
                log.error("CIRCUIT BREAKER TRIPPED - Initiating PANIC close")
                await self._panic_close("Circuit breaker tripped")

            await asyncio.sleep(self._check_interval)

    async def _transport_loop(self):
        """Listen for events from Trading Core via transport."""
        try:
            from core.events.transport import EventTransport

            transport = EventTransport(process_name="guardian")

            # Register event handlers
            def on_heartbeat(event):
                if event.payload.get("source") == "trading_core":
                    self.heartbeat_monitor.record_heartbeat()
                    log.debug("Heartbeat received from trading_core")

            def on_fill(event):
                log.info(f"Fill event from Core: {event.payload}")

            def on_panic(event):
                log.error(f"PANIC event received: {event.payload}")
                # Schedule panic close in async context
                asyncio.create_task(self._panic_close(event.payload.get("reason", "External PANIC")))

            transport.subscribe("HEARTBEAT", on_heartbeat)
            transport.subscribe("FILL", on_fill)
            transport.subscribe("PANIC", on_panic)

            # Start background reader
            transport.start_reader()
            log.info("Transport reader started, listening for events")

            # Keep running until stopped
            while self._running:
                await asyncio.sleep(1)

            transport.stop_reader()

        except Exception as e:
            log.error(f"Transport loop error: {e}")

    async def _panic_close(self, reason: str):
        """
        PANIC: Close ALL positions immediately.
        """
        log.error(f"PANIC CLOSE INITIATED: {reason}")

        # Publish PANIC event
        try:
            from core.events.transport import publish_event
            publish_event(
                "PANIC",
                {"reason": reason, "source": "guardian"},
                source="guardian",
            )
        except Exception:
            pass

        # Get all positions
        positions = self.position_monitor.get_all_positions()
        if not positions:
            positions = self.position_monitor.load_positions()

        # Close all
        closed = await self.binance.close_all_positions(positions)

        # Create STOP.flag
        stop_flag = PROJECT_ROOT / "state" / "STOP.flag"
        stop_flag.write_text(f"PANIC: {reason}\nTime: {datetime.now(timezone.utc).isoformat()}")

        log.error(f"PANIC CLOSE COMPLETE: {closed} positions closed, STOP.flag created")

        # Stop Guardian
        self._running = False

    async def run_forever(self):
        """Run until shutdown."""
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    async def stop(self):
        """Stop Guardian."""
        log.info("Guardian stopping...")
        self._running = False
        log.info("Guardian stopped")

    def get_status(self) -> Dict[str, Any]:
        """Get Guardian status."""
        return {
            "mode": self.mode.value,
            "running": self._running,
            "heartbeat": self.heartbeat_monitor.get_status(),
            "circuit_breaker": self.circuit_breaker.get_status(),
            "positions_tracked": len(self.position_monitor.get_all_positions()),
        }


# === HTTP API ===

def create_http_app(guardian: Guardian):
    """Create FastAPI app for Guardian."""
    try:
        from fastapi import FastAPI
        app = FastAPI(title="HOPE Guardian", version="1.0")

        @app.get("/health")
        async def health():
            return {"status": "healthy", "service": "guardian"}

        @app.get("/status")
        async def status():
            return guardian.get_status()

        @app.post("/panic")
        async def trigger_panic(reason: str = "Manual trigger"):
            await guardian._panic_close(reason)
            return {"status": "panic_triggered"}

        return app
    except ImportError:
        return None


# === Main ===

async def main(mode: GuardianMode):
    """Main entry point."""
    guardian = Guardian(mode=mode)

    # Start guardian
    await guardian.start()

    # Start HTTP server
    app = create_http_app(guardian)
    if app:
        try:
            import uvicorn
            config = uvicorn.Config(app, host="127.0.0.1", port=8104, log_level="warning")
            server = uvicorn.Server(config)
            asyncio.create_task(server.serve())
            log.info("HTTP API started on :8101")
        except ImportError:
            pass

    # Run forever
    await guardian.run_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HOPE Guardian")
    parser.add_argument("--mode", choices=["DRY", "TESTNET", "LIVE"], default="DRY")
    args = parser.parse_args()

    mode = GuardianMode[args.mode]
    asyncio.run(main(mode))
