# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 09:25:00 UTC
# Purpose: Core AI-Gateway components
# === END SIGNATURE ===
"""
AI-Gateway Core Components.

Modules:
- event_bus: Central pub/sub event system
- decision_engine: Policy-based trading decisions
"""

from .event_bus import (
    EventBus,
    EventType,
    Event,
    Subscription,
    get_event_bus,
    publish,
    subscribe,
)

from .decision_engine import (
    DecisionEngine,
    Decision,
    Action,
    SkipReason,
    SignalContext,
    PolicyConfig,
    get_decision_engine,
    evaluate_signal,
)

from .signal_processor import (
    SignalProcessor,
    get_signal_processor,
)

__all__ = [
    # Event Bus
    "EventBus",
    "EventType",
    "Event",
    "Subscription",
    "get_event_bus",
    "publish",
    "subscribe",
    # Decision Engine
    "DecisionEngine",
    "Decision",
    "Action",
    "SkipReason",
    "SignalContext",
    "PolicyConfig",
    "get_decision_engine",
    "evaluate_signal",
    # Signal Processor
    "SignalProcessor",
    "get_signal_processor",
]
