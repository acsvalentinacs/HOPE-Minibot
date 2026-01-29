# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 18:30:00 UTC
# Purpose: Bridge Binance WS price feed to OutcomeTracker for self-improving loop
# Contract: EventBus integration, fail-closed, auto-subscribe tracked symbols
# === END SIGNATURE ===
"""
Price Feed Bridge - Connects real-time prices to OutcomeTracker.

This is the CRITICAL component for the self-improving loop:
    Binance WS -> EventBus PRICE -> PriceFeedBridge -> OutcomeTracker -> MFE/MAE

Without this bridge, outcomes cannot be computed and the AI cannot learn.

INVARIANTS:
- Auto-subscribe to symbols from active tracked signals
- Update prices every price tick (real-time)
- Fail-closed: no prices = outcomes stuck in pending
- Background task manages lifecycle
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class BridgeStats:
    """Bridge operational statistics."""
    started_at: float = 0.0
    price_updates_received: int = 0
    outcomes_completed: int = 0
    last_price_update: float = 0.0
    symbols_tracked: int = 0
    feed_connected: bool = False
    errors: int = 0


class PriceFeedBridge:
    """
    Bridges Binance WebSocket prices to OutcomeTracker.

    Usage:
        bridge = PriceFeedBridge(state_dir=Path("state/ai"))

        # Start the bridge (runs in background)
        await bridge.start()

        # Check status
        print(bridge.get_stats())

        # Stop
        await bridge.stop()
    """

    def __init__(
        self,
        state_dir: Path = Path("state/ai"),
        update_interval: float = 1.0,  # Seconds between outcome updates
        auto_subscribe: bool = True,    # Auto-subscribe to tracked symbols
    ):
        """
        Initialize price bridge.

        Args:
            state_dir: Base state directory
            update_interval: Interval for updating outcomes
            auto_subscribe: Auto-subscribe to tracked symbols
        """
        self.state_dir = Path(state_dir)
        self.update_interval = update_interval
        self.auto_subscribe = auto_subscribe

        # Components (lazy-loaded)
        self._feed = None
        self._outcome_tracker = None
        self._event_bus = None
        self._self_improver = None

        # Current prices cache
        self._prices: Dict[str, float] = {}
        self._last_update: Dict[str, float] = {}

        # State
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._stats = BridgeStats()

        # Event subscription
        self._subscription = None

        logger.info("PriceFeedBridge initialized")

    def _get_feed(self):
        """Lazy-load price feed."""
        if self._feed is None:
            from .binance_ws import get_price_feed, BinancePriceFeed
            self._feed = get_price_feed(event_bus=self._get_event_bus())
        return self._feed

    def _get_event_bus(self):
        """Lazy-load event bus."""
        if self._event_bus is None:
            from ..core.event_bus import get_event_bus
            self._event_bus = get_event_bus()
        return self._event_bus

    def _get_outcome_tracker(self):
        """Lazy-load outcome tracker."""
        if self._outcome_tracker is None:
            from ..modules.self_improver.outcome_tracker import OutcomeTracker
            self._outcome_tracker = OutcomeTracker(
                state_dir=self.state_dir / "outcomes"
            )
        return self._outcome_tracker

    async def start(self) -> bool:
        """
        Start the price bridge.

        Returns:
            True if started successfully
        """
        if self._running:
            logger.warning("PriceFeedBridge already running")
            return True

        try:
            # Initialize components
            feed = self._get_feed()
            bus = self._get_event_bus()
            tracker = self._get_outcome_tracker()

            # Subscribe to PRICE events
            from ..core.event_bus import EventType
            self._subscription = bus.subscribe(
                [EventType.PRICE],
                self._on_price_event,
            )

            # Start background tasks
            self._running = True
            self._stats.started_at = time.time()

            # Task 1: Run price feed
            self._feed_task = asyncio.create_task(
                self._run_feed_loop(),
                name="price_feed_loop"
            )

            # Task 2: Update outcomes periodically
            self._update_task = asyncio.create_task(
                self._run_update_loop(),
                name="outcome_update_loop"
            )

            logger.info("PriceFeedBridge started")
            return True

        except Exception as e:
            logger.error(f"Failed to start PriceFeedBridge: {e}")
            self._stats.errors += 1
            return False

    async def stop(self) -> None:
        """Stop the price bridge."""
        if not self._running:
            return

        self._running = False

        # Cancel tasks
        if hasattr(self, '_feed_task') and self._feed_task:
            self._feed_task.cancel()
            try:
                await self._feed_task
            except asyncio.CancelledError:
                pass

        if hasattr(self, '_update_task') and self._update_task:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass

        # Stop price feed
        if self._feed:
            await self._feed.stop()

        # Unsubscribe from events
        if self._subscription:
            self._get_event_bus().unsubscribe(self._subscription)

        logger.info("PriceFeedBridge stopped")

    async def _run_feed_loop(self) -> None:
        """Run the price feed in background."""
        feed = self._get_feed()

        # Initial subscription to tracked symbols
        if self.auto_subscribe:
            await self._sync_subscriptions()

        try:
            # Run the WebSocket feed
            await feed.run()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Price feed error: {e}")
            self._stats.errors += 1

    async def _run_update_loop(self) -> None:
        """Periodically update outcomes with cached prices."""
        tracker = self._get_outcome_tracker()

        # Lazy-load alert manager
        alert_manager = None
        try:
            from ..alerts.telegram_alerts import get_alert_manager
            alert_manager = get_alert_manager()
        except ImportError:
            logger.debug("Alert manager not available")

        while self._running:
            try:
                # Sync subscriptions with tracked symbols
                if self.auto_subscribe:
                    await self._sync_subscriptions()

                # Update outcomes with current prices
                if self._prices:
                    completed = tracker.update_prices(self._prices.copy())
                    if completed > 0:
                        self._stats.outcomes_completed += completed
                        logger.info(f"Completed {completed} outcomes")

                        # Publish outcome events
                        await self._publish_outcomes(completed)

                # Check alerts for active signals
                if alert_manager:
                    await self._check_alerts(tracker, alert_manager)

                # Update stats
                self._stats.symbols_tracked = len(self._prices)
                self._stats.feed_connected = self._feed.is_connected if self._feed else False

                await asyncio.sleep(self.update_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Update loop error: {e}")
                self._stats.errors += 1
                await asyncio.sleep(5.0)  # Back off on error

    async def _sync_subscriptions(self) -> None:
        """Sync price feed subscriptions with tracked symbols."""
        tracker = self._get_outcome_tracker()
        feed = self._get_feed()

        # Get symbols being tracked
        tracked_symbols = tracker.active_symbols

        # Get symbols already subscribed
        subscribed = feed.symbols

        # Subscribe to missing symbols
        missing = tracked_symbols - subscribed
        if missing:
            await feed.subscribe(list(missing))
            logger.debug(f"Subscribed to {len(missing)} new symbols")

    def _on_price_event(self, event) -> None:
        """Handle PRICE event from EventBus."""
        try:
            payload = event.payload
            symbol = payload.get("symbol")
            price = payload.get("price")

            if symbol and price:
                self._prices[symbol] = price
                self._last_update[symbol] = time.time()
                self._stats.price_updates_received += 1
                self._stats.last_price_update = time.time()

        except Exception as e:
            logger.error(f"Price event handling error: {e}")
            self._stats.errors += 1

    async def _publish_outcomes(self, count: int) -> None:
        """Publish outcome events to EventBus."""
        try:
            from ..core.event_bus import EventType
            bus = self._get_event_bus()

            await bus.publish_async(
                EventType.OUTCOME,
                {
                    "completed_count": count,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
                source="price_bridge",
            )
        except Exception as e:
            logger.error(f"Failed to publish outcome event: {e}")

    async def _check_alerts(self, tracker, alert_manager) -> None:
        """Check active signals for alert thresholds."""
        try:
            # Get active signals from tracker
            active = list(tracker._active.values())
            if not active:
                return

            # Check each signal for MFE/MAE thresholds
            for signal in active:
                current_price = self._prices.get(signal.symbol)
                await alert_manager.check_and_alert(
                    symbol=signal.symbol,
                    mfe=signal.mfe,
                    mae=signal.mae,
                    entry_price=signal.entry_price,
                    current_price=current_price,
                )

            # Check circuit breaker (avg MAE across all signals)
            if len(active) >= 3:
                avg_mae = sum(s.mae for s in active) / len(active)
                await alert_manager.check_circuit_breaker(avg_mae, len(active))

        except Exception as e:
            logger.debug(f"Alert check error: {e}")

    def update_price(self, symbol: str, price: float) -> None:
        """
        Manually update a price (for testing or REST fallback).

        Args:
            symbol: Trading pair
            price: Current price
        """
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"

        self._prices[symbol] = price
        self._last_update[symbol] = time.time()
        self._stats.price_updates_received += 1

    def get_price(self, symbol: str) -> Optional[float]:
        """Get cached price for symbol."""
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"
        return self._prices.get(symbol)

    def get_all_prices(self) -> Dict[str, float]:
        """Get all cached prices."""
        return self._prices.copy()

    def get_stats(self) -> Dict[str, Any]:
        """Get bridge statistics."""
        uptime = time.time() - self._stats.started_at if self._stats.started_at > 0 else 0

        # Get outcome tracker stats
        tracker_stats = {}
        if self._outcome_tracker:
            tracker_stats = self._outcome_tracker.get_stats()

        return {
            "running": self._running,
            "uptime_seconds": round(uptime),
            "feed_connected": self._stats.feed_connected,
            "price_updates": self._stats.price_updates_received,
            "outcomes_completed": self._stats.outcomes_completed,
            "symbols_tracked": self._stats.symbols_tracked,
            "last_price_age_sec": (
                round(time.time() - self._stats.last_price_update)
                if self._stats.last_price_update > 0 else None
            ),
            "errors": self._stats.errors,
            "tracker": tracker_stats,
        }

    @property
    def is_running(self) -> bool:
        """Check if bridge is running."""
        return self._running

    @property
    def is_connected(self) -> bool:
        """Check if price feed is connected."""
        return self._feed.is_connected if self._feed else False


# === Singleton Instance ===

_bridge: Optional[PriceFeedBridge] = None


def get_price_bridge(state_dir: Optional[Path] = None) -> PriceFeedBridge:
    """
    Get or create singleton price bridge.

    Args:
        state_dir: Override state directory (only on first call)

    Returns:
        PriceFeedBridge instance
    """
    global _bridge

    if _bridge is None:
        if state_dir is None:
            state_dir = Path(__file__).resolve().parent.parent.parent / "state" / "ai"
        _bridge = PriceFeedBridge(state_dir=state_dir)

    return _bridge


async def start_bridge() -> bool:
    """Convenience function to start singleton bridge."""
    return await get_price_bridge().start()


async def stop_bridge() -> None:
    """Convenience function to stop singleton bridge."""
    await get_price_bridge().stop()


# === REST Fallback Integration ===

async def update_prices_from_rest(symbols: List[str]) -> int:
    """
    Fetch prices via REST and update bridge.

    Use when WebSocket is unavailable.

    Args:
        symbols: Symbols to fetch

    Returns:
        Number of prices updated
    """
    from .binance_ws import fetch_prices_rest

    bridge = get_price_bridge()
    prices = await fetch_prices_rest(symbols)

    for symbol, price in prices.items():
        bridge.update_price(symbol, price)

    return len(prices)
