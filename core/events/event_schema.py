# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-02-02 15:25:00 UTC
# Modified by: Claude (opus-4.5)
# Modified at: 2026-02-02 17:15:00 UTC
# Purpose: Event schema definitions for HOPE Event-Driven system
# Changes: Added complete trading cycle events (P0 integration), fixed dataclass inheritance
# === END SIGNATURE ===
"""
HOPE Event Schema - Canonical event definitions.

EVENT CHAIN (Full Trading Cycle):
1. SignalReceivedEvent  - Signal enters system (TG/detector)
2. SignalScoredEvent    - After momentum/filters/MTF
3. DecisionEvent        - Eye of God BUY/SKIP
4. OrderIntentEvent     - Intent to execute order
5. OrderSubmittedEvent  - Sent to exchange
6. FillEvent            - Order filled
7. PositionSnapshotEvent- Periodic position state
8. CloseEvent           - Position closed
9. RiskStopEvent        - Circuit breaker triggered
10. StopLossFailureEvent - SL didn't trigger (anomaly)

All events have:
- event_id: sha256 hash (unique, deterministic)
- correlation_id: links signal -> decision -> order -> fill
- timestamp: ISO 8601 UTC
- schema_version: for backwards compatibility
- source: which service produced this event
- payload: event-specific data
"""

import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List


SCHEMA_VERSION = "2.1"


def create_correlation_id(prefix: str = "corr") -> str:
    """Create unique correlation ID for tracing across the pipeline."""
    ts = int(time.time() * 1000)
    return f"{prefix}_{ts}"


def _now_iso() -> str:
    """Get current time in ISO format."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class HopeEvent:
    """
    Base event for HOPE Event-Driven system.

    All events inherit from this and add specific payload fields.
    """
    event_type: str              # SIGNAL, DECISION, ORDER, FILL, CLOSE, HEALTH
    correlation_id: str          # Links related events across the pipeline
    timestamp: str = ""          # ISO 8601 UTC format (auto-filled if empty)
    payload: Dict[str, Any] = field(default_factory=dict)
    source: str = ""             # Service that produced this event
    schema_version: str = SCHEMA_VERSION
    event_id: str = ""           # sha256:xxxx (computed if not provided)

    def __post_init__(self):
        """Compute event_id and timestamp if not provided."""
        if not self.timestamp:
            self.timestamp = _now_iso()
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


# =============================================================================
# FACTORY FUNCTIONS (use these instead of subclasses for simplicity)
# =============================================================================

def make_signal_received(
    correlation_id: str,
    symbol: str,
    source_type: str,
    raw_data: Dict,
) -> HopeEvent:
    """Create SignalReceivedEvent."""
    return HopeEvent(
        event_type="SIGNAL_RECEIVED",
        correlation_id=correlation_id,
        source="gateway",
        payload={
            "symbol": symbol,
            "source_type": source_type,
            "raw_data": raw_data,
            "received_at": _now_iso(),
        },
    )


def make_signal_scored(
    correlation_id: str,
    symbol: str,
    strategy: str,
    direction: str,
    price: float,
    buys_per_sec: float,
    delta_pct: float,
    confidence: float,
    mode: str,
    factors: List[str],
) -> HopeEvent:
    """Create SignalScoredEvent."""
    return HopeEvent(
        event_type="SIGNAL_SCORED",
        correlation_id=correlation_id,
        source="scorer",
        payload={
            "symbol": symbol,
            "strategy": strategy,
            "direction": direction,
            "price": price,
            "buys_per_sec": buys_per_sec,
            "delta_pct": delta_pct,
            "confidence": confidence,
            "mode": mode,
            "factors": factors,
        },
    )


def make_signal(
    correlation_id: str,
    symbol: str,
    strategy: str,
    direction: str,
    price: float,
    buys_per_sec: float = 0,
    delta_pct: float = 0,
    vol_raise_pct: float = 0,
    daily_volume_m: float = 0,
) -> HopeEvent:
    """Create SignalEvent (legacy format)."""
    return HopeEvent(
        event_type="SIGNAL",
        correlation_id=correlation_id,
        source="scanner",
        payload={
            "symbol": symbol,
            "strategy": strategy,
            "direction": direction,
            "price": price,
            "buys_per_sec": buys_per_sec,
            "delta_pct": delta_pct,
            "vol_raise_pct": vol_raise_pct,
            "daily_volume_m": daily_volume_m,
        },
    )


def make_decision(
    correlation_id: str,
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
) -> HopeEvent:
    """Create DecisionEvent."""
    payload = {
        "action": action,
        "symbol": symbol,
        "confidence": confidence,
        "alpha_reasons": alpha_reasons,
        "risk_reasons": risk_reasons,
        "mode": mode,
        "position_size_usdt": position_size_usdt,
        "target_pct": target_pct,
        "stop_pct": stop_pct,
        "timeout_sec": timeout_sec,
    }
    # Add decision hash
    payload["decision_sha256"] = "sha256:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()[:16]

    return HopeEvent(
        event_type="DECISION",
        correlation_id=correlation_id,
        source="eye_of_god",
        payload=payload,
    )


def make_order_intent(
    correlation_id: str,
    symbol: str,
    side: str,
    order_type: str,
    quantity: float,
    price: Optional[float],
    take_profit: float,
    stop_loss: float,
    position_size_usdt: float,
) -> HopeEvent:
    """Create OrderIntentEvent."""
    return HopeEvent(
        event_type="ORDER_INTENT",
        correlation_id=correlation_id,
        source="executor",
        payload={
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": quantity,
            "price": price,
            "take_profit": take_profit,
            "stop_loss": stop_loss,
            "position_size_usdt": position_size_usdt,
            "intent_id": f"intent_{int(time.time()*1000)}",
        },
    )


def make_order_submitted(
    correlation_id: str,
    order_id: str,
    client_order_id: str,
    symbol: str,
    side: str,
    order_type: str,
    quantity: float,
    price: Optional[float],
) -> HopeEvent:
    """Create OrderSubmittedEvent."""
    return HopeEvent(
        event_type="ORDER_SUBMITTED",
        correlation_id=correlation_id,
        source="executor",
        payload={
            "order_id": order_id,
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": quantity,
            "price": price,
            "status": "NEW",
            "submitted_at": _now_iso(),
        },
    )


def make_order(
    correlation_id: str,
    order_id: str,
    symbol: str,
    side: str,
    order_type: str,
    quantity: float,
    price: Optional[float] = None,
    status: str = "NEW",
) -> HopeEvent:
    """Create OrderEvent (legacy format)."""
    return HopeEvent(
        event_type="ORDER",
        correlation_id=correlation_id,
        source="executor",
        payload={
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": quantity,
            "price": price,
            "status": status,
        },
    )


def make_fill(
    correlation_id: str,
    order_id: str,
    symbol: str,
    side: str,
    filled_qty: float,
    avg_price: float,
    commission: float = 0,
    commission_asset: str = "USDT",
    fill_type: str = "FULL",
) -> HopeEvent:
    """Create FillEvent."""
    return HopeEvent(
        event_type="FILL",
        correlation_id=correlation_id,
        source="executor",
        payload={
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "filled_quantity": filled_qty,
            "avg_price": avg_price,
            "commission": commission,
            "commission_asset": commission_asset,
            "fill_type": fill_type,
        },
    )


def make_position_snapshot(
    correlation_id: str,
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
) -> HopeEvent:
    """Create PositionSnapshotEvent."""
    return HopeEvent(
        event_type="POSITION_SNAPSHOT",
        correlation_id=correlation_id,
        source="watchdog",
        payload={
            "position_id": position_id,
            "symbol": symbol,
            "entry_price": entry_price,
            "current_price": current_price,
            "quantity": quantity,
            "pnl_pct": pnl_pct,
            "pnl_usdt": pnl_usdt,
            "mfe": mfe,
            "mae": mae,
            "age_sec": age_sec,
            "stop_price": stop_price,
            "target_price": target_price,
            "status": status,
        },
    )


def make_position_anomaly(
    correlation_id: str,
    position_id: str,
    symbol: str,
    anomaly_type: str,
    expected_state: Dict,
    actual_state: Dict,
    action_taken: str,
) -> HopeEvent:
    """Create PositionAnomalyEvent."""
    return HopeEvent(
        event_type="POSITION_ANOMALY",
        correlation_id=correlation_id,
        source="watchdog",
        payload={
            "position_id": position_id,
            "symbol": symbol,
            "anomaly_type": anomaly_type,
            "expected_state": expected_state,
            "actual_state": actual_state,
            "action_taken": action_taken,
        },
    )


def make_close(
    correlation_id: str,
    position_id: str,
    symbol: str,
    reason: str,
    entry_price: float,
    exit_price: float,
    pnl_pct: float,
    pnl_usdt: float,
    duration_sec: int,
) -> HopeEvent:
    """Create CloseEvent."""
    return HopeEvent(
        event_type="CLOSE",
        correlation_id=correlation_id,
        source="executor",
        payload={
            "position_id": position_id,
            "symbol": symbol,
            "reason": reason,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "pnl_pct": pnl_pct,
            "pnl_usdt": pnl_usdt,
            "duration_sec": duration_sec,
        },
    )


def make_risk_stop(
    correlation_id: str,
    trigger_type: str,
    reason: str,
    losses_count: int,
    losses_total_usdt: float,
    action: str,
    resume_after_sec: int = 0,
) -> HopeEvent:
    """Create RiskStopEvent."""
    return HopeEvent(
        event_type="RISK_STOP",
        correlation_id=correlation_id,
        source="risk_manager",
        payload={
            "trigger_type": trigger_type,
            "reason": reason,
            "losses_count": losses_count,
            "losses_total_usdt": losses_total_usdt,
            "action": action,
            "resume_after_sec": resume_after_sec,
        },
    )


def make_stoploss_failure(
    correlation_id: str,
    position_id: str,
    symbol: str,
    stop_price: float,
    current_price: float,
    duration_below_sl_sec: int,
    action_taken: str,
    close_result: Dict = None,
) -> HopeEvent:
    """Create StopLossFailureEvent."""
    breach_pct = ((stop_price - current_price) / stop_price * 100) if stop_price > 0 else 0
    return HopeEvent(
        event_type="STOPLOSS_FAILURE",
        correlation_id=correlation_id,
        source="watchdog",
        payload={
            "position_id": position_id,
            "symbol": symbol,
            "stop_price": stop_price,
            "current_price": current_price,
            "breach_pct": breach_pct,
            "duration_below_sl_sec": duration_below_sl_sec,
            "action_taken": action_taken,
            "close_result": close_result or {},
        },
    )


def make_health(
    component: str,
    status: str,
    details: Dict,
    latency_ms: int = 0,
) -> HopeEvent:
    """Create HealthEvent."""
    return HopeEvent(
        event_type="HEALTH",
        correlation_id=create_correlation_id("health"),
        source="health_daemon",
        payload={
            "component": component,
            "status": status,
            "details": details,
            "latency_ms": latency_ms,
        },
    )


def make_panic(
    panic_type: str,
    error: str,
    component: str,
    stop_flag_created: bool,
) -> HopeEvent:
    """Create PanicEvent."""
    return HopeEvent(
        event_type="PANIC",
        correlation_id=create_correlation_id("panic"),
        source="system",
        payload={
            "panic_type": panic_type,
            "error": error,
            "component": component,
            "stop_flag_created": stop_flag_created,
            "action_taken": "SYSTEM_HALT" if stop_flag_created else "ALERT_ONLY",
        },
    )


# =============================================================================
# TYPE ALIASES (for type hints in other modules)
# =============================================================================

# Event type strings
SIGNAL_RECEIVED = "SIGNAL_RECEIVED"
SIGNAL_SCORED = "SIGNAL_SCORED"
SIGNAL = "SIGNAL"
DECISION = "DECISION"
ORDER_INTENT = "ORDER_INTENT"
ORDER_SUBMITTED = "ORDER_SUBMITTED"
ORDER = "ORDER"
FILL = "FILL"
POSITION_SNAPSHOT = "POSITION_SNAPSHOT"
POSITION_ANOMALY = "POSITION_ANOMALY"
CLOSE = "CLOSE"
RISK_STOP = "RISK_STOP"
STOPLOSS_FAILURE = "STOPLOSS_FAILURE"
HEALTH = "HEALTH"
PANIC = "PANIC"
