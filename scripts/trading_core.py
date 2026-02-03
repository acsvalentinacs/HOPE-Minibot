# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-02-03T20:20:00Z
# Purpose: Unified Trading Core - combines all trading logic in ONE process
# Contract: Low-latency trading, in-memory event bus, FAIL-CLOSED
# === END SIGNATURE ===
#
# === DEPENDENCIES ===
# READS FROM: state/signals/moonbot_signals.jsonl, AI Gateway artifacts
# WRITES TO: state/events/journal_*.jsonl, state/positions/active.json
# CALLS: ai_gateway/server.py:8100/decision/evaluate, Binance API
# NEXT IN CHAIN: scripts/guardian.py (via Event Transport)
# === END DEPENDENCIES ===
"""
TRADING CORE - The Brain of HOPE

WHAT THIS IS:
    Unified trading process combining:
    - Signal Listener (MoonBot)
    - Signal Classifier (AI scoring)
    - Eye of God (Decision engine)
    - Order Executor (Binance)

WHY ONE PROCESS:
    For scalping, Signal → Decision → Order must be < 100ms.
    HTTP/file calls between components = death.
    Everything in memory, same process, instant.

ARCHITECTURE:
    +----------------------------------------+
    |            TRADING CORE                |
    |                                        |
    |   WebSocket ──► Signals ──► AI Score   |
    |        │                      │        |
    |        v                      v        |
    |   Price Feed           Decision Engine |
    |        │                      │        |
    |        └───────► Order ◄──────┘        |
    |                   │                    |
    |                   v                    |
    |              Position Track            |
    +----------------------------------------+
              │
              v (via Event Transport)
         Guardian / Interface

USAGE:
    python -m scripts.trading_core --mode TESTNET
    python -m scripts.trading_core --mode LIVE --confirm
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import uuid
import signal
import argparse
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional, Set
from enum import Enum

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-5s | CORE | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("TRADING_CORE")


# === Trading Mode ===

class TradingMode(Enum):
    DRY = "DRY"           # No orders, just log
    TESTNET = "TESTNET"   # Binance testnet
    LIVE = "LIVE"         # Real money


# === Correlation ID ===

def generate_correlation_id() -> str:
    """Generate unique correlation ID for event tracing."""
    ts = int(time.time() * 1000)
    rand = uuid.uuid4().hex[:8]
    return f"corr_{ts}_{rand}"


# === In-Memory Event Bus (same process, instant) ===

class InMemoryEventBus:
    """
    In-process event bus for instant communication.

    NOT for cross-process - use EventTransport for that.
    """

    def __init__(self):
        self._handlers: Dict[str, List[Callable]] = {}
        self._queue: asyncio.Queue = None

    async def start(self):
        """Start event processing."""
        self._queue = asyncio.Queue()
        log.info("InMemoryEventBus started")

    def subscribe(self, event_type: str, handler: Callable):
        """Subscribe to event type."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    async def emit(self, event_type: str, data: Dict[str, Any], correlation_id: str = None):
        """Emit event to all subscribers (instant, same process)."""
        event = {
            "type": event_type,
            "data": data,
            "correlation_id": correlation_id or generate_correlation_id(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        handlers = self._handlers.get(event_type, [])
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
            except Exception as e:
                log.error(f"Handler error for {event_type}: {e}")


# === Signal Listener ===

@dataclass
class Signal:
    """Trading signal from MoonBot or other source."""
    signal_id: str
    symbol: str
    direction: str  # Long / Short
    strategy: str
    delta_pct: float
    price: float
    timestamp: str
    source: str = "moonbot"
    correlation_id: str = field(default_factory=generate_correlation_id)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SignalListener:
    """
    Listen for trading signals from MoonBot.

    Sources:
    - WebSocket from MoonBot
    - File-based signals (fallback)
    - HTTP API signals
    """

    def __init__(self, event_bus: InMemoryEventBus):
        self.event_bus = event_bus
        self.signals_dir = PROJECT_ROOT / "state" / "signals"
        self.signals_dir.mkdir(parents=True, exist_ok=True)
        self._last_position = 0
        self._running = False

    async def start(self):
        """Start listening for signals."""
        self._running = True
        log.info("SignalListener started")

        # Poll for signals
        while self._running:
            try:
                signals = await self._poll_signals()
                for signal in signals:
                    await self.event_bus.emit(
                        "SIGNAL_RECEIVED",
                        signal.to_dict(),
                        signal.correlation_id,
                    )
            except Exception as e:
                log.error(f"Signal poll error: {e}")

            await asyncio.sleep(0.1)  # 100ms poll

    async def _poll_signals(self) -> List[Signal]:
        """Poll for new signals from file."""
        signals = []
        signal_file = self.signals_dir / "pending.jsonl"

        if not signal_file.exists():
            return signals

        try:
            with open(signal_file, "r", encoding="utf-8") as f:
                lines = f.readlines()

            for i, line in enumerate(lines[self._last_position:], start=self._last_position):
                try:
                    data = json.loads(line.strip())
                    signal = Signal(
                        signal_id=data.get("signal_id", f"sig_{uuid.uuid4().hex[:8]}"),
                        symbol=data.get("symbol", "UNKNOWN"),
                        direction=data.get("direction", "Long"),
                        strategy=data.get("strategy", "Unknown"),
                        delta_pct=float(data.get("delta_pct", 0)),
                        price=float(data.get("price", 0)),
                        timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
                        source=data.get("source", "moonbot"),
                    )
                    signals.append(signal)
                    self._last_position = i + 1
                except (json.JSONDecodeError, KeyError) as e:
                    log.warning(f"Invalid signal at line {i}: {e}")

        except Exception as e:
            log.error(f"Failed to read signals: {e}")

        return signals

    def stop(self):
        """Stop listening."""
        self._running = False


# === AI Scorer ===

class AIScorer:
    """
    Score signals using AI Gateway.

    Calls: ai_gateway:8100/decision/evaluate
    """

    def __init__(self, event_bus: InMemoryEventBus, gateway_url: str = "http://127.0.0.1:8100"):
        self.event_bus = event_bus
        self.gateway_url = gateway_url
        self._session = None

    async def start(self):
        """Initialize HTTP session."""
        try:
            import aiohttp
            self._session = aiohttp.ClientSession()
            log.info(f"AIScorer connected to {self.gateway_url}")
        except ImportError:
            log.warning("aiohttp not installed, AI scoring disabled")

    async def score_signal(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """
        Score signal through AI Gateway.

        Returns:
            {"win_probability": 0.0-1.0, "should_trade": bool, "reasons": [...]}
        """
        if not self._session:
            return {"win_probability": 0.5, "should_trade": True, "reasons": ["AI unavailable"]}

        try:
            async with self._session.post(
                f"{self.gateway_url}/decision/evaluate",
                json=signal,
                timeout=5.0,
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    await self.event_bus.emit(
                        "SIGNAL_SCORED",
                        {"signal_id": signal.get("signal_id"), "score": result},
                        signal.get("correlation_id"),
                    )
                    return result
                else:
                    log.warning(f"AI Gateway returned {resp.status}")
                    return {"win_probability": 0.5, "should_trade": False, "reasons": [f"HTTP {resp.status}"]}

        except Exception as e:
            log.error(f"AI scoring failed: {e}")
            return {"win_probability": 0.0, "should_trade": False, "reasons": [str(e)]}

    async def stop(self):
        """Close session."""
        if self._session:
            await self._session.close()


# === Decision Engine ===

@dataclass
class Decision:
    """Trading decision."""
    action: str  # BUY, SELL, SKIP
    confidence: float
    reasons: List[str]
    position_size_pct: float = 1.0
    stop_loss_pct: float = -1.0
    take_profit_pct: float = 1.5
    correlation_id: str = ""


class DecisionEngine:
    """
    Eye of God - Makes trading decisions.

    Combines:
    - AI score
    - Market regime
    - Risk limits
    - Position limits
    """

    def __init__(
        self,
        event_bus: InMemoryEventBus,
        mode: TradingMode,
        max_positions: int = 5,
        min_win_probability: float = 0.55,
    ):
        self.event_bus = event_bus
        self.mode = mode
        self.max_positions = max_positions
        self.min_win_probability = min_win_probability
        self._active_positions = 0

    async def decide(self, signal: Dict[str, Any], ai_score: Dict[str, Any]) -> Decision:
        """
        Make trading decision.

        FAIL-CLOSED: Any doubt = SKIP
        """
        correlation_id = signal.get("correlation_id", generate_correlation_id())
        reasons = []

        # Check position limits
        if self._active_positions >= self.max_positions:
            reasons.append(f"MAX_POSITIONS:{self._active_positions}/{self.max_positions}")
            return Decision("SKIP", 0.0, reasons, correlation_id=correlation_id)

        # Check AI score
        win_prob = ai_score.get("win_probability", 0.0)
        if win_prob < self.min_win_probability:
            reasons.append(f"LOW_WIN_PROB:{win_prob:.2f}<{self.min_win_probability}")
            return Decision("SKIP", win_prob, reasons, correlation_id=correlation_id)

        # Check AI recommendation
        if not ai_score.get("should_trade", False):
            reasons.extend(ai_score.get("reasons", ["AI_REJECTED"]))
            return Decision("SKIP", win_prob, reasons, correlation_id=correlation_id)

        # Decide action based on direction
        direction = signal.get("direction", "Long")
        action = "BUY" if direction == "Long" else "SELL"

        # Calculate position size based on confidence
        size_pct = min(1.0, win_prob)  # Scale with confidence

        decision = Decision(
            action=action,
            confidence=win_prob,
            reasons=[f"AI_APPROVED:{win_prob:.2f}"],
            position_size_pct=size_pct,
            correlation_id=correlation_id,
        )

        # Emit decision event
        await self.event_bus.emit(
            "DECISION",
            {
                "signal_id": signal.get("signal_id"),
                "action": decision.action,
                "confidence": decision.confidence,
                "reasons": decision.reasons,
            },
            correlation_id,
        )

        return decision

    def add_position(self):
        """Track new position."""
        self._active_positions += 1

    def remove_position(self):
        """Track closed position."""
        self._active_positions = max(0, self._active_positions - 1)


# === Order Executor ===

class OrderExecutor:
    """
    Execute orders on Binance.

    Modes:
    - DRY: Log only
    - TESTNET: Binance testnet
    - LIVE: Real money
    """

    def __init__(
        self,
        event_bus: InMemoryEventBus,
        mode: TradingMode,
        position_size_usdt: float = 10.0,
    ):
        self.event_bus = event_bus
        self.mode = mode
        self.position_size_usdt = position_size_usdt
        self._client = None

    async def start(self):
        """Initialize Binance client."""
        if self.mode == TradingMode.DRY:
            log.info("OrderExecutor: DRY mode, no Binance client")
            return

        try:
            from binance.client import Client
            from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET

            # Load credentials
            api_key = os.getenv("BINANCE_API_KEY")
            api_secret = os.getenv("BINANCE_API_SECRET")

            if not api_key or not api_secret:
                log.error("Missing Binance API credentials")
                return

            testnet = self.mode == TradingMode.TESTNET
            self._client = Client(api_key, api_secret, testnet=testnet)

            log.info(f"OrderExecutor: Connected to Binance ({'testnet' if testnet else 'LIVE'})")

        except ImportError:
            log.warning("python-binance not installed")
        except Exception as e:
            log.error(f"Failed to init Binance client: {e}")

    async def execute(self, signal: Dict[str, Any], decision: Decision) -> Optional[Dict[str, Any]]:
        """
        Execute order.

        Returns:
            Order result or None if failed/skipped
        """
        correlation_id = decision.correlation_id
        symbol = signal.get("symbol", "")

        if decision.action == "SKIP":
            return None

        # Calculate quantity
        price = signal.get("price", 0)
        if price <= 0:
            log.error(f"Invalid price: {price}")
            return None

        qty_usdt = self.position_size_usdt * decision.position_size_pct
        quantity = qty_usdt / price

        # Emit order intent
        await self.event_bus.emit(
            "ORDER_INTENT",
            {
                "symbol": symbol,
                "side": decision.action,
                "quantity": quantity,
                "price": price,
            },
            correlation_id,
        )

        # DRY mode - just log
        if self.mode == TradingMode.DRY:
            log.info(f"[DRY] Would {decision.action} {quantity:.6f} {symbol} @ ${price}")
            order_result = {
                "orderId": f"dry_{uuid.uuid4().hex[:8]}",
                "symbol": symbol,
                "side": decision.action,
                "executedQty": str(quantity),
                "price": str(price),
                "status": "FILLED",
                "mode": "DRY",
            }
        else:
            # Real execution
            if not self._client:
                log.error("Binance client not initialized")
                return None

            try:
                from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET

                side = SIDE_BUY if decision.action == "BUY" else SIDE_SELL

                order_result = self._client.create_order(
                    symbol=symbol,
                    side=side,
                    type=ORDER_TYPE_MARKET,
                    quantity=round(quantity, 6),
                )

                log.info(f"[{self.mode.value}] Executed {decision.action} {quantity:.6f} {symbol}")

            except Exception as e:
                log.error(f"Order failed: {e}")
                return None

        # Emit fill event
        await self.event_bus.emit(
            "FILL",
            {
                "order_id": order_result.get("orderId"),
                "symbol": symbol,
                "side": decision.action,
                "quantity": float(order_result.get("executedQty", quantity)),
                "price": float(order_result.get("price", price)),
                "status": order_result.get("status", "FILLED"),
            },
            correlation_id,
        )

        return order_result


# === Position Tracker ===

@dataclass
class Position:
    """Active position."""
    position_id: str
    symbol: str
    side: str
    entry_price: float
    quantity: float
    opened_at: str
    correlation_id: str
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None


class PositionTracker:
    """Track active positions."""

    def __init__(self, event_bus: InMemoryEventBus):
        self.event_bus = event_bus
        self.positions: Dict[str, Position] = {}
        self.state_file = PROJECT_ROOT / "state" / "positions" / "active.json"
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        # Load existing positions
        self._load_positions()

    def _load_positions(self):
        """Load positions from disk."""
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                for pos_data in data.get("positions", []):
                    pos = Position(**pos_data)
                    self.positions[pos.position_id] = pos
                log.info(f"Loaded {len(self.positions)} positions")
            except Exception as e:
                log.warning(f"Failed to load positions: {e}")

    def _save_positions(self):
        """Save positions to disk (atomic)."""
        try:
            data = {
                "positions": [asdict(p) for p in self.positions.values()],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            tmp = self.state_file.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.state_file)
        except Exception as e:
            log.error(f"Failed to save positions: {e}")

    def open_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        quantity: float,
        correlation_id: str,
        stop_loss: float = None,
        take_profit: float = None,
    ) -> Position:
        """Open new position."""
        position = Position(
            position_id=f"pos_{uuid.uuid4().hex[:8]}",
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            opened_at=datetime.now(timezone.utc).isoformat(),
            correlation_id=correlation_id,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

        self.positions[position.position_id] = position
        self._save_positions()

        log.info(f"Opened position: {position.position_id} {symbol} {side}")
        return position

    def close_position(self, position_id: str, exit_price: float, reason: str):
        """Close position."""
        if position_id not in self.positions:
            log.warning(f"Position not found: {position_id}")
            return

        position = self.positions.pop(position_id)
        self._save_positions()

        # Calculate PnL
        pnl_pct = ((exit_price - position.entry_price) / position.entry_price) * 100
        if position.side == "SELL":
            pnl_pct = -pnl_pct

        log.info(f"Closed position: {position_id} {position.symbol} PnL: {pnl_pct:.2f}%")

    def get_active_count(self) -> int:
        """Get number of active positions."""
        return len(self.positions)


# === Heartbeat ===

class HeartbeatEmitter:
    """Emit heartbeats for Guardian to monitor."""

    def __init__(self, event_bus: InMemoryEventBus, interval_sec: float = 5.0):
        self.event_bus = event_bus
        self.interval = interval_sec
        self._running = False

    async def start(self):
        """Start heartbeat loop."""
        self._running = True
        log.info(f"Heartbeat started (interval={self.interval}s)")

        while self._running:
            try:
                # Emit via transport for Guardian to see
                from core.events.transport import publish_event
                publish_event(
                    "HEARTBEAT",
                    {
                        "source": "trading_core",
                        "timestamp": time.time(),
                        "status": "healthy",
                    },
                    source="trading_core",
                )
            except Exception as e:
                log.error(f"Heartbeat emit failed: {e}")

            await asyncio.sleep(self.interval)

    def stop(self):
        """Stop heartbeat."""
        self._running = False


# === Main Trading Core ===

class TradingCore:
    """
    Unified Trading Core - all components in one process.

    This is the "brain" of HOPE.
    """

    def __init__(self, mode: TradingMode = TradingMode.DRY):
        self.mode = mode
        self.event_bus = InMemoryEventBus()

        # Components
        self.signal_listener = SignalListener(self.event_bus)
        self.ai_scorer = AIScorer(self.event_bus)
        self.decision_engine = DecisionEngine(self.event_bus, mode)
        self.order_executor = OrderExecutor(self.event_bus, mode)
        self.position_tracker = PositionTracker(self.event_bus)
        self.heartbeat = HeartbeatEmitter(self.event_bus)

        # State
        self._running = False
        self._tasks: List[asyncio.Task] = []

        # Wire up event handlers
        self._setup_handlers()

    def _setup_handlers(self):
        """Setup internal event handlers."""
        self.event_bus.subscribe("SIGNAL_RECEIVED", self._on_signal)
        self.event_bus.subscribe("FILL", self._on_fill)

    async def _on_signal(self, event: Dict):
        """Handle incoming signal."""
        signal = event["data"]
        correlation_id = event["correlation_id"]

        log.info(f"Signal received: {signal.get('symbol')} {signal.get('direction')}")

        # Score with AI
        ai_score = await self.ai_scorer.score_signal(signal)

        # Make decision
        decision = await self.decision_engine.decide(signal, ai_score)

        if decision.action != "SKIP":
            # Execute order
            result = await self.order_executor.execute(signal, decision)
            if result:
                self.decision_engine.add_position()

    async def _on_fill(self, event: Dict):
        """Handle fill event."""
        fill = event["data"]
        correlation_id = event["correlation_id"]

        # Track position
        self.position_tracker.open_position(
            symbol=fill.get("symbol"),
            side=fill.get("side"),
            entry_price=fill.get("price"),
            quantity=fill.get("quantity"),
            correlation_id=correlation_id,
        )

        # Publish to transport for Guardian
        try:
            from core.events.transport import publish_event
            publish_event("FILL", fill, correlation_id, source="trading_core")
        except Exception as e:
            log.error(f"Failed to publish FILL to transport: {e}")

    async def start(self):
        """Start Trading Core."""
        log.info("=" * 60)
        log.info(f"  TRADING CORE - STARTING")
        log.info(f"  Mode: {self.mode.value}")
        log.info("=" * 60)

        self._running = True

        # Start event bus
        await self.event_bus.start()

        # Start components
        await self.ai_scorer.start()
        await self.order_executor.start()

        # Start background tasks
        self._tasks.append(asyncio.create_task(self.signal_listener.start()))
        self._tasks.append(asyncio.create_task(self.heartbeat.start()))

        log.info("Trading Core started")

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
        """Stop Trading Core."""
        log.info("Trading Core stopping...")

        self._running = False
        self.signal_listener.stop()
        self.heartbeat.stop()

        # Cancel tasks
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        await self.ai_scorer.stop()

        log.info("Trading Core stopped")

    def get_status(self) -> Dict[str, Any]:
        """Get current status."""
        return {
            "mode": self.mode.value,
            "running": self._running,
            "active_positions": self.position_tracker.get_active_count(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# === HTTP API ===

def create_http_app(core: TradingCore):
    """Create FastAPI app for Trading Core."""
    try:
        from fastapi import FastAPI
        app = FastAPI(title="HOPE Trading Core", version="1.0")

        @app.get("/health")
        async def health():
            return {"status": "healthy", "service": "trading_core"}

        @app.get("/status")
        async def status():
            return core.get_status()

        return app
    except ImportError:
        log.warning("FastAPI not installed, HTTP API disabled")
        return None


# === Main ===

async def main(mode: TradingMode):
    """Main entry point."""
    core = TradingCore(mode=mode)

    # Setup signal handlers
    loop = asyncio.get_event_loop()

    def shutdown_handler():
        log.info("Shutdown signal received")
        asyncio.create_task(core.stop())

    if os.name != 'nt':
        loop.add_signal_handler(signal.SIGTERM, shutdown_handler)
        loop.add_signal_handler(signal.SIGINT, shutdown_handler)

    # Start core
    await core.start()

    # Start HTTP server
    app = create_http_app(core)
    if app:
        try:
            import uvicorn
            config = uvicorn.Config(app, host="127.0.0.1", port=8103, log_level="warning")
            server = uvicorn.Server(config)
            asyncio.create_task(server.serve())
            log.info("HTTP API started on :8100")
        except ImportError:
            log.warning("uvicorn not installed, HTTP API disabled")

    # Run forever
    await core.run_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HOPE Trading Core")
    parser.add_argument("--mode", choices=["DRY", "TESTNET", "LIVE"], default="DRY")
    parser.add_argument("--confirm", action="store_true", help="Confirm LIVE mode")
    args = parser.parse_args()

    mode = TradingMode[args.mode]

    if mode == TradingMode.LIVE and not args.confirm:
        print("ERROR: LIVE mode requires --confirm flag")
        sys.exit(1)

    asyncio.run(main(mode))
