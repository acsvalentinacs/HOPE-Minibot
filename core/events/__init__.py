# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-02-02 15:25:00 UTC
# Purpose: HOPE Event-Driven System - Core Module
# === END SIGNATURE ===
"""
HOPE Event-Driven System

Components:
- HopeEvent: Base event class with sha256 signatures
- HopeEventBus: In-memory async event bus
- HybridEventLogger: Dual-write to bus + file

Usage:
    from core.events import get_event_bus, SignalEvent

    bus = get_event_bus()
    bus.subscribe("SIGNAL", my_handler)
    await bus.publish(SignalEvent(...))
"""

from .event_schema import (
    HopeEvent,
    SignalEvent,
    DecisionEvent,
    OrderEvent,
    FillEvent,
    SCHEMA_VERSION,
)

from .event_bus import (
    HopeEventBus,
    get_event_bus,
)

__all__ = [
    "HopeEvent",
    "SignalEvent",
    "DecisionEvent",
    "OrderEvent",
    "FillEvent",
    "SCHEMA_VERSION",
    "HopeEventBus",
    "get_event_bus",
]
