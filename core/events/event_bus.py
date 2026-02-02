# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-02-02 15:25:00 UTC
# Purpose: In-memory event bus for HOPE Event-Driven system
# === END SIGNATURE ===
"""
HOPE Event Bus - Async in-memory pub/sub system.

Features:
- Pub/Sub pattern with wildcard support
- Async handlers
- Dead Letter Queue for failed deliveries
- Graceful shutdown

Usage:
    bus = get_event_bus()

    # Subscribe to events
    @bus.on("SIGNAL")
    async def handle_signal(event):
        print(f"Got signal: {event.payload}")

    # Or manual subscribe
    bus.subscribe("DECISION", my_handler)

    # Publish events
    await bus.publish(SignalEvent(...))

    # Run the bus (in main async loop)
    await bus.run()
"""

import asyncio
import logging
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Callable, Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .event_schema import HopeEvent

log = logging.getLogger("EVENT_BUS")


class HopeEventBus:
    """
    In-memory event bus for HOPE trading system.

    Provides:
    - Fast async pub/sub (ms latency)
    - Wildcard subscriptions ("*" matches all)
    - Dead Letter Queue for failed events
    - Event persistence to JSONL files
    """

    def __init__(self, persist_dir: Optional[Path] = None):
        """
        Initialize event bus.

        Args:
            persist_dir: Directory to persist events (optional).
                        If provided, all events are written to JSONL files.
        """
        self._subscribers: Dict[str, List[Callable]] = defaultdict(list)
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._dlq: asyncio.Queue = asyncio.Queue(maxsize=100)  # Dead Letter Queue
        self._running = False
        self._persist_dir = persist_dir

        if persist_dir:
            persist_dir.mkdir(parents=True, exist_ok=True)

        self._stats = {
            "events_published": 0,
            "events_delivered": 0,
            "events_failed": 0,
        }

    def subscribe(self, event_type: str, handler: Callable):
        """
        Subscribe handler to event type.

        Args:
            event_type: Event type to subscribe to (e.g., "SIGNAL", "DECISION")
                       Use "*" for wildcard (receive all events)
            handler: Async or sync function to handle events
        """
        self._subscribers[event_type].append(handler)
        log.debug(f"Subscribed {handler.__name__} to {event_type}")

    def unsubscribe(self, event_type: str, handler: Callable):
        """Unsubscribe handler from event type."""
        if handler in self._subscribers[event_type]:
            self._subscribers[event_type].remove(handler)
            log.debug(f"Unsubscribed {handler.__name__} from {event_type}")

    def on(self, event_type: str):
        """
        Decorator to subscribe handler to event type.

        Usage:
            @bus.on("SIGNAL")
            async def handle_signal(event):
                ...
        """
        def decorator(handler: Callable):
            self.subscribe(event_type, handler)
            return handler
        return decorator

    async def publish(self, event: 'HopeEvent'):
        """
        Publish event to bus.

        Event is queued and delivered asynchronously to all subscribers.
        Also persisted to file if persist_dir is configured.
        """
        await self._queue.put(event)
        self._stats["events_published"] += 1
        log.debug(f"Published {event.event_type}: {event.event_id}")

        # Persist to file if configured
        if self._persist_dir:
            self._persist_event(event)

    def publish_sync(self, event: 'HopeEvent'):
        """
        Synchronous publish (for non-async contexts).

        Note: Use publish() in async contexts for better performance.
        """
        try:
            self._queue.put_nowait(event)
            self._stats["events_published"] += 1
            if self._persist_dir:
                self._persist_event(event)
        except asyncio.QueueFull:
            log.error(f"Event bus queue full, dropping event: {event.event_id}")

    def _persist_event(self, event: 'HopeEvent'):
        """Persist event to JSONL file."""
        if not self._persist_dir:
            return

        file_name = f"{event.event_type.lower()}_events.jsonl"
        file_path = self._persist_dir / file_name

        try:
            with open(file_path, 'a', encoding='utf-8') as f:
                f.write(event.to_json() + '\n')
        except Exception as e:
            log.error(f"Failed to persist event: {e}")

    async def _dispatch(self, event: 'HopeEvent'):
        """Dispatch event to all subscribers."""
        # Get handlers for specific event type
        handlers = self._subscribers.get(event.event_type, []).copy()
        # Add wildcard handlers
        handlers.extend(self._subscribers.get("*", []))

        if not handlers:
            log.debug(f"No handlers for {event.event_type}")
            return

        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
                self._stats["events_delivered"] += 1
            except Exception as e:
                log.error(f"Handler {handler.__name__} failed for {event.event_id}: {e}")
                self._stats["events_failed"] += 1
                # Add to Dead Letter Queue
                await self._dlq.put({
                    "event": event.to_dict(),
                    "error": str(e),
                    "handler": handler.__name__,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

    async def run(self):
        """
        Run event processing loop.

        Call this from your main async function:
            await bus.run()
        """
        self._running = True
        log.info("Event Bus started")

        while self._running:
            try:
                # Wait for event with timeout (allows checking _running flag)
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._dispatch(event)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                log.info("Event Bus cancelled")
                break
            except Exception as e:
                log.error(f"Event Bus error: {e}")

        log.info("Event Bus stopped")

    def stop(self):
        """Stop the event bus gracefully."""
        self._running = False

    def get_stats(self) -> Dict[str, int]:
        """Get event bus statistics."""
        return {
            **self._stats,
            "queue_size": self._queue.qsize(),
            "dlq_size": self._dlq.qsize(),
            "subscriber_count": sum(len(h) for h in self._subscribers.values()),
        }

    async def get_dlq_events(self, limit: int = 10) -> List[Dict]:
        """Get events from Dead Letter Queue."""
        events = []
        while not self._dlq.empty() and len(events) < limit:
            try:
                events.append(self._dlq.get_nowait())
            except asyncio.QueueEmpty:
                break
        return events


# Singleton instance
_event_bus: Optional[HopeEventBus] = None


def get_event_bus(persist_dir: Optional[Path] = None) -> HopeEventBus:
    """
    Get singleton event bus instance.

    Args:
        persist_dir: Directory to persist events (only used on first call)

    Returns:
        HopeEventBus instance
    """
    global _event_bus
    if _event_bus is None:
        _event_bus = HopeEventBus(persist_dir=persist_dir)
    return _event_bus


def reset_event_bus():
    """Reset singleton (for testing)."""
    global _event_bus
    if _event_bus:
        _event_bus.stop()
    _event_bus = None
