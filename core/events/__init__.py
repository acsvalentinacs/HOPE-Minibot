# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-02-02 15:25:00 UTC
# Modified by: Claude (opus-4.5)
# Modified at: 2026-02-02 17:20:00 UTC
# Purpose: HOPE Event-Driven System - Core Module
# Changes: Added all trading cycle events for P0 integration
# === END SIGNATURE ===
"""
HOPE Event-Driven System

Usage:
    from core.events import (
        get_event_bus,
        create_correlation_id,
        make_signal_received,
        make_decision,
        make_fill,
        make_close,
    )

    # Get bus singleton
    bus = get_event_bus()

    # Create correlation ID for tracing
    corr_id = create_correlation_id("sig")

    # Publish event
    event = make_signal_received(corr_id, "BTCUSDT", "moonbot", {...})
    await bus.publish(event)

    # Subscribe to events
    @bus.on("DECISION")
    async def handle_decision(event):
        print(f"Decision: {event.payload}")
"""

from .event_schema import (
    # Base
    HopeEvent,
    SCHEMA_VERSION,
    create_correlation_id,
    # Factory functions
    make_signal_received,
    make_signal_scored,
    make_signal,
    make_decision,
    make_order_intent,
    make_order_submitted,
    make_order,
    make_fill,
    make_position_snapshot,
    make_position_anomaly,
    make_close,
    make_risk_stop,
    make_stoploss_failure,
    make_health,
    make_panic,
    # Event type constants
    SIGNAL_RECEIVED,
    SIGNAL_SCORED,
    SIGNAL,
    DECISION,
    ORDER_INTENT,
    ORDER_SUBMITTED,
    ORDER,
    FILL,
    POSITION_SNAPSHOT,
    POSITION_ANOMALY,
    CLOSE,
    RISK_STOP,
    STOPLOSS_FAILURE,
    HEALTH,
    PANIC,
)

from .event_bus import (
    HopeEventBus,
    get_event_bus,
    reset_event_bus,
    check_stop_flag,
    clear_stop_flag,
)

from .integration import (
    EventPublisher,
    get_publisher,
    check_bus_health,
    start_bus_background,
)

__all__ = [
    # Base
    "HopeEvent",
    "SCHEMA_VERSION",
    "create_correlation_id",
    # Factory functions
    "make_signal_received",
    "make_signal_scored",
    "make_signal",
    "make_decision",
    "make_order_intent",
    "make_order_submitted",
    "make_order",
    "make_fill",
    "make_position_snapshot",
    "make_position_anomaly",
    "make_close",
    "make_risk_stop",
    "make_stoploss_failure",
    "make_health",
    "make_panic",
    # Event type constants
    "SIGNAL_RECEIVED",
    "SIGNAL_SCORED",
    "SIGNAL",
    "DECISION",
    "ORDER_INTENT",
    "ORDER_SUBMITTED",
    "ORDER",
    "FILL",
    "POSITION_SNAPSHOT",
    "POSITION_ANOMALY",
    "CLOSE",
    "RISK_STOP",
    "STOPLOSS_FAILURE",
    "HEALTH",
    "PANIC",
    # Bus
    "HopeEventBus",
    "get_event_bus",
    "reset_event_bus",
    "check_stop_flag",
    "clear_stop_flag",
    # Integration
    "EventPublisher",
    "get_publisher",
    "check_bus_health",
    "start_bus_background",
]
