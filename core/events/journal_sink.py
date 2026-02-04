# === AI SIGNATURE ===
# Module: core/events/journal_sink.py
# Created by: Claude (opus-4.5)
# Created at: 2026-02-04 18:30:00 UTC
# Purpose: Bridge between Event Bus and Event Journal
# === END SIGNATURE ===
"""
Event Journal Sink

Connects existing Event Bus to Event Journal for persistent logging.
All events published through Event Bus are automatically written to Journal.
"""

from pathlib import Path
from typing import Optional
from datetime import datetime, timezone
import sys

# Add hope_core to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hope_core"))

try:
    from hope_core.journal.event_journal import EventJournal, EventType, EventLevel
    JOURNAL_AVAILABLE = True
except ImportError:
    JOURNAL_AVAILABLE = False

# Global journal instance
_journal: Optional["EventJournal"] = None


def init_journal(journal_path: Optional[Path] = None) -> Optional["EventJournal"]:
    """
    Initialize global Event Journal.

    Args:
        journal_path: Path to journal file. Default: state/events/journal.jsonl

    Returns:
        EventJournal instance or None if not available
    """
    global _journal

    if not JOURNAL_AVAILABLE:
        return None

    if journal_path is None:
        journal_path = Path("state/events/journal.jsonl")

    journal_path.parent.mkdir(parents=True, exist_ok=True)
    _journal = EventJournal(journal_path)
    return _journal


def get_journal() -> Optional["EventJournal"]:
    """Get global Event Journal instance."""
    return _journal


def log_event(
    event_type: str,
    correlation_id: str,
    payload: dict,
    level: str = "INFO",
    symbol: Optional[str] = None,
    order_id: Optional[str] = None,
) -> Optional[str]:
    """
    Log event to Journal.

    Args:
        event_type: Type of event (SIGNAL_RECEIVED, ORDER_FILLED, etc.)
        correlation_id: Correlation ID linking related events
        payload: Event data
        level: INFO, WARNING, ERROR, CRITICAL
        symbol: Trading symbol (if applicable)
        order_id: Order ID (if applicable)

    Returns:
        Event ID or None if journal not available
    """
    if _journal is None:
        return None

    try:
        # Map level string to EventLevel
        level_map = {
            "DEBUG": EventLevel.DEBUG,
            "INFO": EventLevel.INFO,
            "WARNING": EventLevel.WARNING,
            "ERROR": EventLevel.ERROR,
            "CRITICAL": EventLevel.CRITICAL,
        }
        event_level = level_map.get(level.upper(), EventLevel.INFO)

        # Log to journal
        event = _journal.append(
            event_type=event_type,
            payload=payload,
            correlation_id=correlation_id,
            level=event_level,
            symbol=symbol,
            order_id=order_id,
        )
        return event.id
    except Exception as e:
        print(f"[JOURNAL] Failed to log event: {e}")
        return None


# Convenience functions for common event types
def log_signal(correlation_id: str, symbol: str, data: dict) -> Optional[str]:
    """Log signal received event."""
    return log_event(
        event_type="SIGNAL_RECEIVED",
        correlation_id=correlation_id,
        payload=data,
        symbol=symbol,
    )


def log_decision(correlation_id: str, symbol: str, action: str, confidence: float, reasons: list) -> Optional[str]:
    """Log decision made event."""
    return log_event(
        event_type="DECISION_MADE",
        correlation_id=correlation_id,
        payload={
            "action": action,
            "confidence": confidence,
            "reasons": reasons,
        },
        symbol=symbol,
    )


def log_order(correlation_id: str, symbol: str, order_id: str, side: str, qty: float, price: float) -> Optional[str]:
    """Log order sent event."""
    return log_event(
        event_type="ORDER_SENT",
        correlation_id=correlation_id,
        payload={
            "side": side,
            "quantity": qty,
            "price": price,
        },
        symbol=symbol,
        order_id=order_id,
    )


def log_fill(correlation_id: str, symbol: str, order_id: str, filled_qty: float, avg_price: float) -> Optional[str]:
    """Log order filled event."""
    return log_event(
        event_type="ORDER_FILLED",
        correlation_id=correlation_id,
        payload={
            "filled_qty": filled_qty,
            "avg_price": avg_price,
        },
        symbol=symbol,
        order_id=order_id,
    )


def log_position_closed(correlation_id: str, symbol: str, pnl_pct: float, reason: str) -> Optional[str]:
    """Log position closed event."""
    return log_event(
        event_type="POSITION_CLOSED",
        correlation_id=correlation_id,
        payload={
            "pnl_pct": pnl_pct,
            "reason": reason,
        },
        symbol=symbol,
    )


def log_error(correlation_id: str, error: str, context: dict = None) -> Optional[str]:
    """Log error event."""
    return log_event(
        event_type="ERROR",
        correlation_id=correlation_id,
        payload={
            "error": error,
            "context": context or {},
        },
        level="ERROR",
    )
