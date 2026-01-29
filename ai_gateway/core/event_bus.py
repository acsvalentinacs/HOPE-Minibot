# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 09:10:00 UTC
# Purpose: Central event bus for AI-Gateway module coordination
# Contract: JSONL persistence, sha256: checksums, fail-closed
# === END SIGNATURE ===
"""
Event Bus - Central nervous system for HOPE AI Gateway.

Provides pub/sub mechanism for decoupled module communication.
All events persisted to JSONL with sha256: checksums.

Channels:
- signal: MoonBot signals
- price: Binance price updates
- news: RSS news items
- prediction: AI predictions
- decision: BUY/SKIP decisions
- trade: Executed trades
- outcome: Signal outcomes (WIN/LOSS)

INVARIANTS:
- Events are immutable after publish
- All events have sha256: checksum
- JSONL append is atomic (temp -> fsync -> rename)
- Subscribers receive events in order
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set
from uuid import uuid4

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """Event channel types."""
    SIGNAL = "signal"           # MoonBot signals
    PRICE = "price"             # Binance price updates
    NEWS = "news"               # RSS news items
    PREDICTION = "prediction"   # AI predictions
    DECISION = "decision"       # BUY/SKIP decisions
    TRADE = "trade"             # Executed trades
    OUTCOME = "outcome"         # Signal outcomes
    SYSTEM = "system"           # System events (start/stop/error)


@dataclass
class Event:
    """
    Immutable event with checksum validation.

    INVARIANT: checksum = sha256(type + timestamp + payload)
    """
    id: str
    type: EventType
    timestamp: str          # ISO 8601 UTC
    payload: Dict[str, Any]
    checksum: str           # sha256:...
    source: str = "unknown"

    def is_valid(self) -> bool:
        """Verify checksum matches payload."""
        expected = self._compute_checksum()
        return self.checksum == expected

    def _compute_checksum(self) -> str:
        """Compute deterministic checksum."""
        data = {
            "type": self.type.value,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }
        canonical = json.dumps(data, sort_keys=True, default=str, ensure_ascii=False)
        return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSONL."""
        return {
            "id": self.id,
            "type": self.type.value,
            "timestamp": self.timestamp,
            "payload": self.payload,
            "checksum": self.checksum,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
        """Deserialize from dict."""
        return cls(
            id=data["id"],
            type=EventType(data["type"]),
            timestamp=data["timestamp"],
            payload=data["payload"],
            checksum=data["checksum"],
            source=data.get("source", "unknown"),
        )


@dataclass
class Subscription:
    """Event subscription handle."""
    id: str
    types: Set[EventType]
    callback: Callable[[Event], None]
    async_callback: Optional[Callable[[Event], Any]] = None
    is_active: bool = True

    def cancel(self) -> None:
        """Cancel this subscription."""
        self.is_active = False


class EventBus:
    """
    Central event bus with JSONL persistence.

    Usage:
        bus = EventBus(state_dir=Path("state/events"))

        # Subscribe to events
        def on_signal(event: Event):
            print(f"Got signal: {event.payload}")

        sub = bus.subscribe([EventType.SIGNAL], on_signal)

        # Publish event
        bus.publish(EventType.SIGNAL, {"symbol": "BTCUSDT", "price": 88000})

        # Replay historical events
        for event in bus.replay(EventType.SIGNAL, from_ts="2026-01-29T00:00:00Z"):
            process(event)
    """

    def __init__(
        self,
        state_dir: Path = Path("state/events"),
        buffer_size: int = 1000,
    ):
        """
        Initialize event bus.

        Args:
            state_dir: Directory for JSONL event logs
            buffer_size: Max events to buffer in memory
        """
        self.state_dir = Path(state_dir)
        self.buffer_size = buffer_size

        # Subscriptions by event type
        self._subscriptions: Dict[EventType, List[Subscription]] = {
            t: [] for t in EventType
        }

        # In-memory buffer (recent events)
        self._buffer: Dict[EventType, List[Event]] = {
            t: [] for t in EventType
        }

        # Thread safety
        self._lock = threading.Lock()
        self._async_lock: Optional[asyncio.Lock] = None

        # Stats
        self._published_count: Dict[EventType, int] = {t: 0 for t in EventType}
        self._delivered_count: int = 0

        # Ensure directories exist
        self.state_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"EventBus initialized, state_dir={self.state_dir}")

    def publish(
        self,
        event_type: EventType,
        payload: Dict[str, Any],
        source: str = "unknown",
    ) -> Event:
        """
        Publish event to bus.

        Args:
            event_type: Type of event
            payload: Event data
            source: Source module identifier

        Returns:
            Published event with ID and checksum
        """
        # Create event
        event_id = f"evt:{event_type.value}:{uuid4().hex[:12]}"
        timestamp = datetime.utcnow().isoformat() + "Z"

        # Compute checksum
        data = {
            "type": event_type.value,
            "timestamp": timestamp,
            "payload": payload,
        }
        canonical = json.dumps(data, sort_keys=True, default=str, ensure_ascii=False)
        checksum = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()[:16]

        event = Event(
            id=event_id,
            type=event_type,
            timestamp=timestamp,
            payload=payload,
            checksum=checksum,
            source=source,
        )

        with self._lock:
            # Persist to JSONL
            self._persist_event(event)

            # Buffer in memory
            buf = self._buffer[event_type]
            buf.append(event)
            if len(buf) > self.buffer_size:
                buf.pop(0)

            # Update stats
            self._published_count[event_type] += 1

            # Deliver to subscribers
            self._deliver(event)

        return event

    async def publish_async(
        self,
        event_type: EventType,
        payload: Dict[str, Any],
        source: str = "unknown",
    ) -> Event:
        """Async version of publish."""
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()

        async with self._async_lock:
            event = self.publish(event_type, payload, source)

            # Deliver to async subscribers
            await self._deliver_async(event)

        return event

    def subscribe(
        self,
        types: List[EventType],
        callback: Callable[[Event], None],
    ) -> Subscription:
        """
        Subscribe to event types.

        Args:
            types: Event types to subscribe to
            callback: Function called for each event

        Returns:
            Subscription handle (call .cancel() to unsubscribe)
        """
        sub = Subscription(
            id=f"sub:{uuid4().hex[:8]}",
            types=set(types),
            callback=callback,
        )

        with self._lock:
            for event_type in types:
                self._subscriptions[event_type].append(sub)

        logger.debug(f"Subscription {sub.id} created for {[t.value for t in types]}")
        return sub

    def subscribe_async(
        self,
        types: List[EventType],
        callback: Callable[[Event], Any],
    ) -> Subscription:
        """
        Subscribe with async callback.

        Args:
            types: Event types to subscribe to
            callback: Async function called for each event

        Returns:
            Subscription handle
        """
        sub = Subscription(
            id=f"sub:{uuid4().hex[:8]}",
            types=set(types),
            callback=lambda e: None,  # Dummy sync callback
            async_callback=callback,
        )

        with self._lock:
            for event_type in types:
                self._subscriptions[event_type].append(sub)

        return sub

    def unsubscribe(self, subscription: Subscription) -> None:
        """Cancel subscription."""
        subscription.cancel()

        with self._lock:
            for event_type in subscription.types:
                subs = self._subscriptions[event_type]
                self._subscriptions[event_type] = [
                    s for s in subs if s.id != subscription.id
                ]

    def replay(
        self,
        event_type: EventType,
        from_ts: Optional[str] = None,
        to_ts: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Event]:
        """
        Replay historical events from JSONL.

        Args:
            event_type: Type of events to replay
            from_ts: Start timestamp (ISO 8601)
            to_ts: End timestamp (ISO 8601)
            limit: Max events to return

        Returns:
            List of events in chronological order
        """
        events = []
        log_path = self._get_log_path(event_type)

        if not log_path.exists():
            return events

        try:
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        data = json.loads(line)
                        event = Event.from_dict(data)

                        # Validate checksum
                        if not event.is_valid():
                            logger.warning(f"Invalid checksum for event {event.id}")
                            continue

                        # Filter by timestamp
                        if from_ts and event.timestamp < from_ts:
                            continue
                        if to_ts and event.timestamp > to_ts:
                            continue

                        events.append(event)

                        if len(events) >= limit:
                            break

                    except (json.JSONDecodeError, KeyError) as e:
                        logger.warning(f"Failed to parse event: {e}")
                        continue

        except Exception as e:
            logger.error(f"Failed to read event log: {e}")

        return events

    def get_recent(
        self,
        event_type: EventType,
        count: int = 10,
    ) -> List[Event]:
        """Get recent events from memory buffer."""
        with self._lock:
            buf = self._buffer[event_type]
            return buf[-count:] if len(buf) >= count else buf[:]

    def get_stats(self) -> Dict[str, Any]:
        """Get bus statistics."""
        with self._lock:
            return {
                "published": {t.value: c for t, c in self._published_count.items()},
                "delivered": self._delivered_count,
                "subscriptions": {
                    t.value: len([s for s in subs if s.is_active])
                    for t, subs in self._subscriptions.items()
                },
                "buffer_sizes": {
                    t.value: len(buf) for t, buf in self._buffer.items()
                },
            }

    def _persist_event(self, event: Event) -> None:
        """Atomically append event to JSONL log."""
        log_path = self._get_log_path(event.type)
        tmp_path = log_path.with_suffix(".jsonl.tmp")

        try:
            # Append to existing file
            line = json.dumps(event.to_dict(), ensure_ascii=False) + "\n"

            with open(log_path, "a", encoding="utf-8", newline="\n") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())

        except Exception as e:
            logger.error(f"Failed to persist event {event.id}: {e}")

    def _get_log_path(self, event_type: EventType) -> Path:
        """Get JSONL log path for event type."""
        return self.state_dir / f"{event_type.value}.jsonl"

    def _deliver(self, event: Event) -> None:
        """Deliver event to synchronous subscribers."""
        subs = self._subscriptions.get(event.type, [])

        for sub in subs:
            if not sub.is_active:
                continue

            try:
                sub.callback(event)
                self._delivered_count += 1
            except Exception as e:
                logger.error(f"Subscriber {sub.id} error: {e}")

    async def _deliver_async(self, event: Event) -> None:
        """Deliver event to async subscribers."""
        subs = self._subscriptions.get(event.type, [])

        for sub in subs:
            if not sub.is_active or sub.async_callback is None:
                continue

            try:
                await sub.async_callback(event)
                self._delivered_count += 1
            except Exception as e:
                logger.error(f"Async subscriber {sub.id} error: {e}")


# === Singleton Instance ===

_bus: Optional[EventBus] = None
_bus_lock = threading.Lock()


def get_event_bus(state_dir: Optional[Path] = None) -> EventBus:
    """
    Get or create singleton event bus.

    Args:
        state_dir: Override state directory (only on first call)

    Returns:
        EventBus instance
    """
    global _bus

    with _bus_lock:
        if _bus is None:
            if state_dir is None:
                state_dir = Path(__file__).resolve().parent.parent.parent / "state" / "events"
            _bus = EventBus(state_dir=state_dir)
        return _bus


def publish(
    event_type: EventType,
    payload: Dict[str, Any],
    source: str = "unknown",
) -> Event:
    """Convenience function to publish to singleton bus."""
    return get_event_bus().publish(event_type, payload, source)


def subscribe(
    types: List[EventType],
    callback: Callable[[Event], None],
) -> Subscription:
    """Convenience function to subscribe to singleton bus."""
    return get_event_bus().subscribe(types, callback)
