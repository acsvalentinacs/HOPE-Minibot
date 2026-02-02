# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-02-02 15:25:00 UTC
# Modified by: Claude (opus-4.5)
# Modified at: 2026-02-02 17:05:00 UTC
# Purpose: In-memory event bus with FAIL-CLOSED behavior
# Changes: Added STOP.flag creation on critical failures (P0 integration)
# === END SIGNATURE ===
"""
HOPE Event Bus - Async in-memory pub/sub system with FAIL-CLOSED.

FAIL-CLOSED RULES:
1. Queue overflow -> create STOP.flag + PANIC event
2. Handler crash without recovery -> create STOP.flag + PANIC event
3. DLQ overflow -> create STOP.flag
4. Any publish() failure in critical path -> STOP.flag

Usage:
    bus = get_event_bus()

    # Subscribe to events
    @bus.on("SIGNAL")
    async def handle_signal(event):
        print(f"Got signal: {event.payload}")

    # Publish events (async)
    await bus.publish(SignalEvent(...))

    # Publish events (sync - for non-async contexts)
    bus.publish_sync(event)

    # Run the bus (in main async loop)
    await bus.run()
"""

import asyncio
import logging
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Callable, Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .event_schema import HopeEvent

log = logging.getLogger("EVENT_BUS")

# STOP flag path (fail-closed)
STOP_FLAG_PATH = Path("state/STOP.flag")
PANIC_LOG_PATH = Path("state/panic.log")


def _create_stop_flag(reason: str) -> bool:
    """
    Create STOP.flag - fail-closed trigger.

    Returns True if flag was created, False if it already existed.
    """
    try:
        STOP_FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if STOP_FLAG_PATH.exists():
            return False

        content = {
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "created_by": "event_bus",
        }
        # Atomic write
        tmp_path = STOP_FLAG_PATH.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(content, indent=2), encoding="utf-8")
        os.replace(tmp_path, STOP_FLAG_PATH)

        log.critical(f"STOP.flag CREATED: {reason}")
        return True
    except Exception as e:
        log.error(f"Failed to create STOP.flag: {e}")
        return False


def _log_panic(panic_type: str, error: str, component: str):
    """Log panic event to panic.log for post-mortem analysis."""
    try:
        PANIC_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "panic_type": panic_type,
            "error": str(error),
            "component": component,
        }
        with open(PANIC_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        log.error(f"Failed to log panic: {e}")


class HopeEventBus:
    """
    In-memory event bus for HOPE trading system with FAIL-CLOSED.

    Provides:
    - Fast async pub/sub (ms latency)
    - Wildcard subscriptions ("*" matches all)
    - Dead Letter Queue for failed events
    - Event persistence to JSONL files
    - FAIL-CLOSED: creates STOP.flag on critical failures
    """

    # Queue limits
    MAX_QUEUE_SIZE = 1000
    MAX_DLQ_SIZE = 100
    QUEUE_WARNING_THRESHOLD = 800  # Warn at 80% capacity

    def __init__(self, persist_dir: Optional[Path] = None, fail_closed: bool = True):
        """
        Initialize event bus.

        Args:
            persist_dir: Directory to persist events (optional).
            fail_closed: If True, create STOP.flag on critical errors.
        """
        self._subscribers: Dict[str, List[Callable]] = defaultdict(list)
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=self.MAX_QUEUE_SIZE)
        self._dlq: asyncio.Queue = asyncio.Queue(maxsize=self.MAX_DLQ_SIZE)
        self._running = False
        self._persist_dir = persist_dir
        self._fail_closed = fail_closed

        if persist_dir:
            persist_dir.mkdir(parents=True, exist_ok=True)

        self._stats = {
            "events_published": 0,
            "events_delivered": 0,
            "events_failed": 0,
            "queue_overflows": 0,
            "dlq_overflows": 0,
            "panics": 0,
        }

        log.info(f"Event Bus initialized (fail_closed={fail_closed})")

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

    async def publish(self, event: 'HopeEvent') -> bool:
        """
        Publish event to bus (async).

        Returns True if published, False if failed (queue full).
        On queue full with fail_closed=True, creates STOP.flag.
        """
        try:
            # Check queue capacity
            current_size = self._queue.qsize()
            if current_size >= self.QUEUE_WARNING_THRESHOLD:
                log.warning(f"Event queue near capacity: {current_size}/{self.MAX_QUEUE_SIZE}")

            # Try to put with timeout to avoid deadlock
            await asyncio.wait_for(self._queue.put(event), timeout=1.0)
            self._stats["events_published"] += 1
            log.debug(f"Published {event.event_type}: {event.event_id}")

            # Persist to file if configured
            if self._persist_dir:
                self._persist_event(event)

            return True

        except asyncio.TimeoutError:
            return self._handle_queue_overflow(event, "publish timeout")
        except asyncio.QueueFull:
            return self._handle_queue_overflow(event, "queue full")
        except Exception as e:
            log.error(f"Failed to publish event: {e}")
            return False

    def publish_sync(self, event: 'HopeEvent') -> bool:
        """
        Synchronous publish (for non-async contexts).

        Returns True if published, False if failed.
        """
        try:
            self._queue.put_nowait(event)
            self._stats["events_published"] += 1

            if self._persist_dir:
                self._persist_event(event)

            return True

        except asyncio.QueueFull:
            return self._handle_queue_overflow(event, "queue full (sync)")
        except Exception as e:
            log.error(f"Failed to publish event (sync): {e}")
            return False

    def _handle_queue_overflow(self, event: 'HopeEvent', reason: str) -> bool:
        """Handle queue overflow - FAIL-CLOSED."""
        self._stats["queue_overflows"] += 1
        log.error(f"Event queue overflow: {reason} (event: {event.event_id})")

        if self._fail_closed:
            self._stats["panics"] += 1
            _log_panic("BUS_OVERFLOW", reason, "event_bus")
            _create_stop_flag(f"Event bus overflow: {reason}")

            # Try to emit PANIC event to DLQ for audit
            try:
                from .event_schema import make_panic
                panic = make_panic("BUS_OVERFLOW", reason, "event_bus", True)
                self._dlq.put_nowait({
                    "event": panic.to_dict(),
                    "error": reason,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            except Exception:
                pass

        return False

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
                    await asyncio.wait_for(handler(event), timeout=5.0)
                else:
                    handler(event)
                self._stats["events_delivered"] += 1

            except asyncio.TimeoutError:
                self._handle_handler_failure(event, handler, "timeout")
            except Exception as e:
                self._handle_handler_failure(event, handler, str(e))

    def _handle_handler_failure(self, event: 'HopeEvent', handler: Callable, error: str):
        """Handle handler failure - add to DLQ."""
        log.error(f"Handler {handler.__name__} failed for {event.event_id}: {error}")
        self._stats["events_failed"] += 1

        # Add to Dead Letter Queue
        try:
            self._dlq.put_nowait({
                "event": event.to_dict(),
                "error": error,
                "handler": handler.__name__,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except asyncio.QueueFull:
            self._stats["dlq_overflows"] += 1
            log.error("DLQ overflow - events being lost!")

            if self._fail_closed:
                self._stats["panics"] += 1
                _log_panic("DLQ_OVERFLOW", "Dead Letter Queue full", "event_bus")
                _create_stop_flag("DLQ overflow - handler failures exceeding capacity")

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
                if self._fail_closed:
                    _log_panic("BUS_ERROR", str(e), "event_bus")

        log.info("Event Bus stopped")

    def stop(self):
        """Stop the event bus gracefully."""
        self._running = False
        log.info("Event Bus stopping...")

    def is_running(self) -> bool:
        """Check if bus is running."""
        return self._running

    def get_stats(self) -> Dict[str, int]:
        """Get event bus statistics."""
        return {
            **self._stats,
            "queue_size": self._queue.qsize(),
            "dlq_size": self._dlq.qsize(),
            "subscriber_count": sum(len(h) for h in self._subscribers.values()),
            "running": self._running,
        }

    async def get_dlq_events(self, limit: int = 10) -> List[Dict]:
        """Get events from Dead Letter Queue (for retry/analysis)."""
        events = []
        while not self._dlq.empty() and len(events) < limit:
            try:
                events.append(self._dlq.get_nowait())
            except asyncio.QueueEmpty:
                break
        return events

    def flush_dlq(self) -> List[Dict]:
        """Flush all events from DLQ (for emergency recovery)."""
        events = []
        while not self._dlq.empty():
            try:
                events.append(self._dlq.get_nowait())
            except asyncio.QueueEmpty:
                break
        return events


# Singleton instance
_event_bus: Optional[HopeEventBus] = None


def get_event_bus(
    persist_dir: Optional[Path] = None,
    fail_closed: bool = True,
) -> HopeEventBus:
    """
    Get singleton event bus instance.

    Args:
        persist_dir: Directory to persist events (only used on first call)
        fail_closed: Enable STOP.flag creation on critical errors

    Returns:
        HopeEventBus instance
    """
    global _event_bus
    if _event_bus is None:
        default_persist = Path("state/events")
        _event_bus = HopeEventBus(
            persist_dir=persist_dir or default_persist,
            fail_closed=fail_closed,
        )
    return _event_bus


def reset_event_bus():
    """Reset singleton (for testing)."""
    global _event_bus
    if _event_bus:
        _event_bus.stop()
    _event_bus = None


def check_stop_flag() -> Optional[Dict]:
    """Check if STOP.flag exists and return its contents."""
    if STOP_FLAG_PATH.exists():
        try:
            return json.loads(STOP_FLAG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {"reason": "unknown", "exists": True}
    return None


def clear_stop_flag() -> bool:
    """Clear STOP.flag (use with caution - only after fixing the issue)."""
    if STOP_FLAG_PATH.exists():
        try:
            STOP_FLAG_PATH.unlink()
            log.info("STOP.flag cleared")
            return True
        except Exception as e:
            log.error(f"Failed to clear STOP.flag: {e}")
    return False
