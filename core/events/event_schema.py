# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-02-02 15:25:00 UTC
# Purpose: Event schema definitions for HOPE Event-Driven system
# === END SIGNATURE ===
"""
HOPE Event Schema - Canonical event definitions.

All events have:
- event_id: sha256 hash (unique, deterministic)
- correlation_id: links signal → decision → order → fill
- timestamp: ISO 8601 UTC
- schema_version: for backwards compatibility
- source: which service produced this event
- payload: event-specific data
"""

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, Any, Optional


SCHEMA_VERSION = "2.0"


@dataclass
class HopeEvent:
    """
    Base event for HOPE Event-Driven system.

    All events inherit from this and add specific payload fields.
    """
    event_type: str              # SIGNAL, DECISION, ORDER, FILL, CLOSE, HEALTH
    correlation_id: str          # Links related events across the pipeline
    timestamp: str               # ISO 8601 UTC format
    payload: Dict[str, Any] = field(default_factory=dict)
    source: str = ""             # Service that produced this event
    schema_version: str = SCHEMA_VERSION
    event_id: str = ""           # sha256:xxxx (computed if not provided)

    def __post_init__(self):
        """Compute event_id if not provided."""
        if not self.event_id:
            self.event_id = self.compute_id()

    def compute_id(self) -> str:
        """
        Compute deterministic sha256 event_id.

        Based on: event_type + correlation_id + timestamp + payload
        """
        data = f"{self.event_type}:{self.correlation_id}:{self.timestamp}:{json.dumps(self.payload, sort_keys=True)}"
        return "sha256:" + hashlib.sha256(data.encode()).hexdigest()[:16]

    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        return asdict(self)

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict) -> 'HopeEvent':
        """Create event from dictionary."""
        return cls(**data)


@dataclass
class SignalEvent(HopeEvent):
    """
    Signal detected by scanner (pump_detector, momentum_trader, moonbot).

    Payload should contain:
    - symbol: str (e.g., "BTCUSDT")
    - strategy: str (e.g., "PumpDetection", "MOMENTUM_24H")
    - direction: str ("Long" or "Short")
    - price: float
    - buys_per_sec: float
    - delta_pct: float
    - vol_raise_pct: float
    - daily_volume_m: float
    """
    event_type: str = "SIGNAL"
    source: str = "scanner"


@dataclass
class DecisionEvent(HopeEvent):
    """
    Decision from Eye of God V3.

    Payload should contain:
    - action: str ("BUY" or "SKIP")
    - symbol: str
    - confidence: float (0.0 - 1.0)
    - reasons: List[str]
    - mode: str ("SCALP", "SWING", etc.)
    - position_size_usdt: float
    - target_pct: float
    - stop_pct: float
    - timeout_sec: int
    """
    event_type: str = "DECISION"
    source: str = "eye_of_god"


@dataclass
class OrderEvent(HopeEvent):
    """
    Order sent to exchange.

    Payload should contain:
    - order_id: str
    - symbol: str
    - side: str ("BUY" or "SELL")
    - type: str ("MARKET", "LIMIT")
    - quantity: float
    - price: Optional[float]
    - status: str ("NEW", "PENDING", "REJECTED")
    """
    event_type: str = "ORDER"
    source: str = "executor"


@dataclass
class FillEvent(HopeEvent):
    """
    Order filled on exchange.

    Payload should contain:
    - order_id: str
    - symbol: str
    - side: str
    - filled_quantity: float
    - avg_price: float
    - commission: float
    - commission_asset: str
    """
    event_type: str = "FILL"
    source: str = "executor"


@dataclass
class CloseEvent(HopeEvent):
    """
    Position closed (by target, stop, or timeout).

    Payload should contain:
    - position_id: str
    - symbol: str
    - reason: str ("TARGET", "STOP", "TIMEOUT", "MANUAL")
    - entry_price: float
    - exit_price: float
    - pnl_pct: float
    - pnl_usdt: float
    """
    event_type: str = "CLOSE"
    source: str = "executor"


@dataclass
class HealthEvent(HopeEvent):
    """
    System health check event.

    Payload should contain:
    - component: str
    - status: str ("PASS", "FAIL", "WARN")
    - details: Dict
    """
    event_type: str = "HEALTH"
    source: str = "health_daemon"


# Helper function to create correlation_id
def create_correlation_id(prefix: str = "corr") -> str:
    """Create unique correlation ID."""
    import time
    ts = int(time.time() * 1000)
    return f"{prefix}_{ts}"
