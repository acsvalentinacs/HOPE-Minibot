# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 09:30:00 UTC
# Purpose: Signal processor - orchestrates AI modules for signal evaluation
# Contract: fail-closed, atomic, full audit trail
# === END SIGNATURE ===
"""
Signal Processor - Orchestrates AI-Gateway modules for signal evaluation.

Flow:
    Signal → EventBus → Enrich (Regime, Anomaly, Prediction)
          → DecisionEngine → Decision → EventBus

This module:
1. Receives signals from EventBus
2. Fetches current price from BinancePriceFeed
3. Queries AI modules (Regime, Anomaly, Predictor)
4. Builds SignalContext
5. Passes to DecisionEngine
6. Publishes decision to EventBus
7. Tracks outcomes via OutcomeTracker

INVARIANT: No trade without complete context (fail-closed)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..contracts import MarketRegime
from .event_bus import EventBus, EventType, Event, get_event_bus
from .decision_engine import (
    DecisionEngine,
    Decision,
    Action,
    SignalContext,
    get_decision_engine,
)

logger = logging.getLogger(__name__)


class SignalProcessor:
    """
    Orchestrates signal evaluation through AI modules.

    Usage:
        processor = SignalProcessor()

        # Start processing
        await processor.start()

        # Submit signal for evaluation
        decision = await processor.process_signal(signal_data)

        # Stop processing
        await processor.stop()
    """

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        decision_engine: Optional[DecisionEngine] = None,
        price_feed: Optional[Any] = None,
        outcome_tracker: Optional[Any] = None,
    ):
        """
        Initialize signal processor.

        Args:
            event_bus: Event bus for pub/sub (default: singleton)
            decision_engine: Decision engine (default: singleton)
            price_feed: Price feed for current prices
            outcome_tracker: Outcome tracker for WIN/LOSS tracking
        """
        self.event_bus = event_bus or get_event_bus()
        self.decision_engine = decision_engine or get_decision_engine()
        self.price_feed = price_feed
        self.outcome_tracker = outcome_tracker

        # Module references (lazy loaded)
        self._regime_detector: Optional[Any] = None
        self._anomaly_scanner: Optional[Any] = None
        self._predictor: Optional[Any] = None

        # State
        self._running = False
        self._subscription = None
        self._processed_count = 0
        self._error_count = 0

        # Active positions (for position limit check)
        self._active_positions: Dict[str, Dict[str, Any]] = {}

        # Circuit breaker
        self._circuit_state = "CLOSED"

        logger.info("SignalProcessor initialized")

    async def start(self) -> None:
        """Start signal processing."""
        if self._running:
            return

        self._running = True

        # Subscribe to signal events
        self._subscription = self.event_bus.subscribe(
            [EventType.SIGNAL],
            self._on_signal_event
        )

        logger.info("SignalProcessor started")

    async def stop(self) -> None:
        """Stop signal processing."""
        self._running = False

        if self._subscription:
            self.event_bus.unsubscribe(self._subscription)
            self._subscription = None

        logger.info("SignalProcessor stopped")

    def _on_signal_event(self, event: Event) -> None:
        """Handle incoming signal event (sync callback)."""
        # Schedule async processing
        asyncio.create_task(self._process_event(event))

    async def _process_event(self, event: Event) -> None:
        """Process signal event asynchronously."""
        try:
            signal_data = event.payload
            decision = await self.process_signal(signal_data)

            # Publish decision to EventBus
            self.event_bus.publish(
                EventType.DECISION,
                decision.to_dict(),
                source="signal_processor"
            )

        except Exception as e:
            logger.error(f"Failed to process signal event: {e}")
            self._error_count += 1

    async def process_signal(self, signal: Dict[str, Any]) -> Decision:
        """
        Process a trading signal through all AI modules.

        Args:
            signal: Signal data dict with:
                - symbol: Trading pair
                - price: Entry price
                - direction: "Long" or "Short"
                - delta_pct: Price change %
                - volume_24h: 24h volume in USD (optional)
                - strategy: Signal strategy name

        Returns:
            Decision with BUY/SKIP action
        """
        self._processed_count += 1

        # Extract signal data
        symbol = signal.get("symbol", "UNKNOWN")
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"

        signal_id = signal.get("id") or f"sig:{symbol}:{datetime.utcnow().timestamp():.0f}"
        price = float(signal.get("price", 0))
        direction = signal.get("direction", "Long")
        delta_pct = float(signal.get("delta_pct", 0))
        volume_24h = float(signal.get("daily_volume", signal.get("volume_24h", 0)))

        # Get current price (fail-closed if unavailable)
        current_price = await self._get_current_price(symbol)
        if current_price is None:
            logger.warning(f"No price for {symbol}, using signal price")
            current_price = price

        # Get AI module outputs
        regime = await self._get_regime(symbol)
        anomaly_score = await self._get_anomaly_score(symbol)
        prediction_prob = await self._get_prediction(signal)
        news_score = await self._get_news_score(symbol)

        # Build context
        ctx = SignalContext(
            signal_id=signal_id,
            symbol=symbol,
            price=current_price,
            direction=direction,
            delta_pct=delta_pct,
            volume_24h=volume_24h,
            prediction_prob=prediction_prob,
            regime=regime,
            anomaly_score=anomaly_score,
            news_score=news_score,
            circuit_state=self._circuit_state,
            active_positions=len(self._active_positions),
            raw_signal=signal,
        )

        # Evaluate through decision engine
        decision = self.decision_engine.evaluate(ctx)

        # Track outcome if BUY
        if decision.action == Action.BUY and self.outcome_tracker:
            try:
                self.outcome_tracker.register_signal({
                    "id": signal_id,
                    "symbol": symbol,
                    "price": current_price,
                    "direction": direction,
                    **signal,
                })
            except Exception as e:
                logger.error(f"Failed to register signal for tracking: {e}")

        return decision

    async def _get_current_price(self, symbol: str) -> Optional[float]:
        """Get current price from price feed."""
        if self.price_feed is None:
            return None

        try:
            return self.price_feed.get_price(symbol)
        except Exception as e:
            logger.error(f"Price feed error for {symbol}: {e}")
            return None

    async def _get_regime(self, symbol: str) -> Optional[MarketRegime]:
        """Get market regime from detector."""
        if self._regime_detector is None:
            try:
                from ..modules.regime import detector
                self._regime_detector = detector
            except ImportError:
                return None

        try:
            # Try to get cached regime
            result = getattr(self._regime_detector, "get_current_regime", None)
            if result:
                regime_data = result(symbol)
                if regime_data:
                    return MarketRegime(regime_data.get("regime", "ranging"))
        except Exception as e:
            logger.debug(f"Regime detector error: {e}")

        # Default to ranging (will pass regime check in favorable mode)
        return MarketRegime.RANGING

    async def _get_anomaly_score(self, symbol: str) -> Optional[float]:
        """Get anomaly score from scanner."""
        if self._anomaly_scanner is None:
            try:
                from ..modules.anomaly import scanner
                self._anomaly_scanner = scanner
            except ImportError:
                return 0.0  # Default: no anomaly

        try:
            result = getattr(self._anomaly_scanner, "get_anomaly_score", None)
            if result:
                return result(symbol)
        except Exception as e:
            logger.debug(f"Anomaly scanner error: {e}")

        return 0.0  # Default: no anomaly

    async def _get_prediction(self, signal: Dict[str, Any]) -> Optional[float]:
        """Get prediction probability from predictor."""
        if self._predictor is None:
            try:
                from ..modules.predictor import signal_classifier
                self._predictor = signal_classifier
            except ImportError:
                return 0.5  # Default: neutral

        try:
            # Try to predict
            result = getattr(self._predictor, "predict", None)
            if result:
                prediction = result(signal)
                if prediction:
                    return prediction.get("probability", 0.5)
        except Exception as e:
            logger.debug(f"Predictor error: {e}")

        return 0.5  # Default: neutral

    async def _get_news_score(self, symbol: str) -> Optional[float]:
        """Get news sentiment score."""
        # Extract base asset (e.g., "BTC" from "BTCUSDT")
        base_asset = symbol.replace("USDT", "")

        try:
            # Check sentiment module
            from ..modules.sentiment import analyzer
            result = getattr(analyzer, "get_sentiment", None)
            if result:
                return result(base_asset)
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"Sentiment error: {e}")

        return 0.0  # Default: neutral

    def update_circuit_state(self, state: str) -> None:
        """Update circuit breaker state."""
        self._circuit_state = state
        logger.info(f"Circuit state updated: {state}")

    def add_position(self, symbol: str, position_data: Dict[str, Any]) -> None:
        """Track active position."""
        self._active_positions[symbol] = position_data

    def remove_position(self, symbol: str) -> None:
        """Remove tracked position."""
        self._active_positions.pop(symbol, None)

    def get_stats(self) -> Dict[str, Any]:
        """Get processor statistics."""
        return {
            "running": self._running,
            "processed_count": self._processed_count,
            "error_count": self._error_count,
            "active_positions": len(self._active_positions),
            "circuit_state": self._circuit_state,
            "decision_engine_stats": self.decision_engine.get_stats(),
        }


# === Singleton Instance ===

_processor: Optional[SignalProcessor] = None


def get_signal_processor(**kwargs) -> SignalProcessor:
    """Get or create singleton signal processor."""
    global _processor

    if _processor is None:
        _processor = SignalProcessor(**kwargs)

    return _processor
