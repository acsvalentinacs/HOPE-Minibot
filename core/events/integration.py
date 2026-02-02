# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-02-02 17:25:00 UTC
# Purpose: Event Bus integration helpers for trading system
# === END SIGNATURE ===
"""
HOPE Event Bus Integration Helpers.

Provides easy-to-use functions for publishing events from trading components.

Usage:
    from core.events.integration import EventPublisher

    # Get publisher singleton
    publisher = EventPublisher.get()

    # Start correlation chain for a signal
    corr_id = publisher.signal_received(symbol, source, raw_data)

    # Publish decision
    publisher.decision(corr_id, symbol, action, confidence, ...)

    # Publish fill
    publisher.fill(corr_id, order_id, symbol, ...)

This module is FAIL-CLOSED:
- If bus not available, logs warning and continues (degraded mode)
- If publish fails, creates STOP.flag via bus
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable

# Import event system
from . import (
    get_event_bus,
    check_stop_flag,
    create_correlation_id,
    HopeEvent,
    make_signal_received,
    make_signal_scored,
    make_decision,
    make_order_intent,
    make_order_submitted,
    make_fill,
    make_position_snapshot,
    make_position_anomaly,
    make_close,
    make_risk_stop,
    make_stoploss_failure,
    make_panic,
)

log = logging.getLogger("EVENT_INTEGRATION")


class EventPublisher:
    """
    Event publishing helper for trading system.

    Features:
    - Singleton pattern for easy access
    - Sync publish for non-async code (most trading code)
    - Automatic correlation ID management
    - Graceful degradation if bus unavailable
    - Latency tracking for p95 monitoring
    """

    _instance: Optional['EventPublisher'] = None

    def __init__(self):
        self._bus = None
        self._enabled = True
        self._latencies: List[float] = []  # Last 100 publish latencies
        self._stats = {
            "events_published": 0,
            "events_failed": 0,
            "degraded_mode": False,
        }

    @classmethod
    def get(cls) -> 'EventPublisher':
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = EventPublisher()
            cls._instance._init_bus()
        return cls._instance

    def _init_bus(self):
        """Initialize event bus connection."""
        try:
            self._bus = get_event_bus()
            log.info("EventPublisher initialized with bus")
        except Exception as e:
            log.warning(f"Failed to get event bus: {e} - entering degraded mode")
            self._stats["degraded_mode"] = True

    def _publish(self, event: HopeEvent) -> bool:
        """
        Publish event (sync version for trading code).

        Returns True if published, False if failed.
        """
        if not self._enabled:
            return False

        if self._bus is None:
            log.debug(f"Bus not available, skipping event: {event.event_type}")
            return False

        start = time.perf_counter_ns()

        try:
            success = self._bus.publish_sync(event)
            latency_ms = (time.perf_counter_ns() - start) / 1_000_000

            # Track latency
            self._latencies.append(latency_ms)
            if len(self._latencies) > 100:
                self._latencies.pop(0)

            if success:
                self._stats["events_published"] += 1
                log.debug(f"Published {event.event_type} ({latency_ms:.1f}ms)")
            else:
                self._stats["events_failed"] += 1
                log.warning(f"Failed to publish {event.event_type}")

            return success

        except Exception as e:
            self._stats["events_failed"] += 1
            log.error(f"Publish error: {e}")
            return False

    def get_latency_p95(self) -> float:
        """Get p95 latency in ms."""
        if not self._latencies:
            return 0.0
        sorted_lat = sorted(self._latencies)
        p95_idx = int(len(sorted_lat) * 0.95)
        return sorted_lat[min(p95_idx, len(sorted_lat) - 1)]

    def get_stats(self) -> Dict[str, Any]:
        """Get publisher statistics."""
        return {
            **self._stats,
            "latency_p95_ms": self.get_latency_p95(),
            "bus_stats": self._bus.get_stats() if self._bus else {},
        }

    def disable(self):
        """Disable event publishing (for testing)."""
        self._enabled = False
        log.info("EventPublisher disabled")

    def enable(self):
        """Enable event publishing."""
        self._enabled = True
        log.info("EventPublisher enabled")

    # =========================================================================
    # SIGNAL EVENTS
    # =========================================================================

    def signal_received(
        self,
        symbol: str,
        source_type: str,
        raw_data: Dict,
    ) -> str:
        """
        Publish SignalReceivedEvent.

        Returns correlation_id for this signal chain.
        """
        corr_id = create_correlation_id("sig")
        event = make_signal_received(corr_id, symbol, source_type, raw_data)
        self._publish(event)
        return corr_id

    def signal_scored(
        self,
        corr_id: str,
        symbol: str,
        strategy: str,
        direction: str,
        price: float,
        buys_per_sec: float,
        delta_pct: float,
        confidence: float,
        mode: str,
        factors: List[str],
    ):
        """Publish SignalScoredEvent."""
        event = make_signal_scored(
            corr_id, symbol, strategy, direction, price,
            buys_per_sec, delta_pct, confidence, mode, factors
        )
        self._publish(event)

    # =========================================================================
    # DECISION EVENTS
    # =========================================================================

    def decision(
        self,
        corr_id: str,
        symbol: str,
        action: str,
        confidence: float,
        alpha_reasons: List[str],
        risk_reasons: List[str],
        mode: str,
        position_size_usdt: float,
        target_pct: float,
        stop_pct: float,
        timeout_sec: int,
    ):
        """Publish DecisionEvent."""
        event = make_decision(
            corr_id, symbol, action, confidence,
            alpha_reasons, risk_reasons, mode,
            position_size_usdt, target_pct, stop_pct, timeout_sec
        )
        self._publish(event)

    # =========================================================================
    # ORDER EVENTS
    # =========================================================================

    def order_intent(
        self,
        corr_id: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float],
        take_profit: float,
        stop_loss: float,
        position_size_usdt: float,
    ):
        """Publish OrderIntentEvent."""
        event = make_order_intent(
            corr_id, symbol, side, order_type, quantity,
            price, take_profit, stop_loss, position_size_usdt
        )
        self._publish(event)

    def order_submitted(
        self,
        corr_id: str,
        order_id: str,
        client_order_id: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float] = None,
    ):
        """Publish OrderSubmittedEvent."""
        event = make_order_submitted(
            corr_id, order_id, client_order_id, symbol,
            side, order_type, quantity, price
        )
        self._publish(event)

    def fill(
        self,
        corr_id: str,
        order_id: str,
        symbol: str,
        side: str,
        filled_qty: float,
        avg_price: float,
        commission: float = 0,
        commission_asset: str = "USDT",
    ):
        """Publish FillEvent."""
        event = make_fill(
            corr_id, order_id, symbol, side,
            filled_qty, avg_price, commission, commission_asset
        )
        self._publish(event)

    # =========================================================================
    # POSITION EVENTS
    # =========================================================================

    def position_snapshot(
        self,
        corr_id: str,
        position_id: str,
        symbol: str,
        entry_price: float,
        current_price: float,
        quantity: float,
        pnl_pct: float,
        pnl_usdt: float,
        mfe: float,
        mae: float,
        age_sec: int,
        stop_price: float,
        target_price: float,
        status: str = "OPEN",
    ):
        """Publish PositionSnapshotEvent."""
        event = make_position_snapshot(
            corr_id, position_id, symbol, entry_price, current_price,
            quantity, pnl_pct, pnl_usdt, mfe, mae, age_sec,
            stop_price, target_price, status
        )
        self._publish(event)

    def position_anomaly(
        self,
        corr_id: str,
        position_id: str,
        symbol: str,
        anomaly_type: str,
        expected_state: Dict,
        actual_state: Dict,
        action_taken: str,
    ):
        """Publish PositionAnomalyEvent."""
        event = make_position_anomaly(
            corr_id, position_id, symbol, anomaly_type,
            expected_state, actual_state, action_taken
        )
        self._publish(event)

    def close(
        self,
        corr_id: str,
        position_id: str,
        symbol: str,
        reason: str,
        entry_price: float,
        exit_price: float,
        pnl_pct: float,
        pnl_usdt: float,
        duration_sec: int,
    ):
        """Publish CloseEvent."""
        event = make_close(
            corr_id, position_id, symbol, reason,
            entry_price, exit_price, pnl_pct, pnl_usdt, duration_sec
        )
        self._publish(event)

    # =========================================================================
    # RISK EVENTS
    # =========================================================================

    def risk_stop(
        self,
        corr_id: str,
        trigger_type: str,
        reason: str,
        losses_count: int,
        losses_total_usdt: float,
        action: str,
        resume_after_sec: int = 0,
    ):
        """Publish RiskStopEvent."""
        event = make_risk_stop(
            corr_id, trigger_type, reason, losses_count,
            losses_total_usdt, action, resume_after_sec
        )
        self._publish(event)

    def stoploss_failure(
        self,
        corr_id: str,
        position_id: str,
        symbol: str,
        stop_price: float,
        current_price: float,
        duration_below_sl_sec: int,
        action_taken: str,
        close_result: Dict = None,
    ):
        """Publish StopLossFailureEvent."""
        event = make_stoploss_failure(
            corr_id, position_id, symbol, stop_price, current_price,
            duration_below_sl_sec, action_taken, close_result
        )
        self._publish(event)

    def panic(
        self,
        panic_type: str,
        error: str,
        component: str,
        stop_flag_created: bool,
    ):
        """Publish PanicEvent."""
        event = make_panic(panic_type, error, component, stop_flag_created)
        self._publish(event)


# =========================================================================
# CONVENIENCE FUNCTIONS
# =========================================================================

def get_publisher() -> EventPublisher:
    """Get EventPublisher singleton."""
    return EventPublisher.get()


def check_bus_health() -> Dict[str, Any]:
    """Check event bus health."""
    publisher = get_publisher()
    stats = publisher.get_stats()

    # Check for STOP.flag
    stop_flag = check_stop_flag()

    return {
        "healthy": not stats.get("degraded_mode", False) and stop_flag is None,
        "stop_flag": stop_flag,
        "stats": stats,
        "latency_p95_ms": publisher.get_latency_p95(),
    }


def start_bus_background() -> asyncio.Task:
    """
    Start event bus in background.

    Call this from main async function:
        task = start_bus_background()
        # ... your code ...
        task.cancel()  # on shutdown
    """
    bus = get_event_bus()
    return asyncio.create_task(bus.run())
