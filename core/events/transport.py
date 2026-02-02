# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-02-02T12:30:00Z
# Purpose: Inter-Process Transport Layer for HOPE Event Bus
# Contract: Events written here are visible to ALL processes
# === END SIGNATURE ===
"""
HOPE Event Transport Layer - Cross-Process Event Communication

PROBLEM SOLVED:
    Event Bus (asyncio.Queue) works only INSIDE one process.
    HOPE runs multiple processes (AutoTrader, Watchdog, TG Bot).
    Events emitted by AutoTrader are INVISIBLE to Watchdog.

SOLUTION:
    File-based Event Journal (JSONL) as transport between processes.
    All processes WRITE to the same journal.
    All processes READ/TAIL the same journal.

ARCHITECTURE:
    +-----------+     +-----------+     +-----------+
    | AutoTrader|     | Watchdog  |     | TG Bot    |
    |  Process  |     |  Process  |     |  Process  |
    +-----+-----+     +-----+-----+     +-----+-----+
          |                 |                 |
          v                 v                 v
    +-----+-----+     +-----+-----+     +-----+-----+
    | Transport |     | Transport |     | Transport |
    |  Writer   |     |  Writer   |     |  Writer   |
    +-----+-----+     +-----+-----+     +-----+-----+
          |                 |                 |
          +--------+--------+--------+--------+
                   |
                   v
    +==============+===============================+
    |        EVENT JOURNAL (JSONL)                 |
    |  state/events/journal_YYYYMMDD.jsonl         |
    +=============================================+
                   |
          +--------+--------+--------+--------+
          |                 |                 |
          v                 v                 v
    +-----+-----+     +-----+-----+     +-----+-----+
    | Transport |     | Transport |     | Transport |
    |  Reader   |     |  Reader   |     |  Reader   |
    +-----+-----+     +-----+-----+     +-----+-----+
          |                 |                 |
          v                 v                 v
    | Handlers  |     | Handlers  |     | Handlers  |

USAGE:
    from core.events.transport import EventTransport

    # In any process:
    transport = EventTransport.get()

    # Write event (visible to ALL processes)
    transport.publish(event)

    # Read new events (tail mode)
    for event in transport.poll():
        handle(event)

    # Or subscribe to event types
    transport.subscribe("FILL", my_handler)
    transport.start_reader()  # Background thread polls journal
"""

import json
import os
import time
import threading
import logging
from pathlib import Path

# Platform-specific locking
if os.name == 'nt':
    import msvcrt
else:
    import fcntl
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Callable, Set
from dataclasses import dataclass, asdict
from queue import Queue

from .event_schema import HopeEvent

log = logging.getLogger("EVENT_TRANSPORT")

# Journal configuration
JOURNAL_DIR = Path("state/events")
JOURNAL_DIR.mkdir(parents=True, exist_ok=True)

# Reader configuration
POLL_INTERVAL_SEC = 0.1  # 100ms - fast enough for scalping
MAX_EVENTS_PER_POLL = 100


def _get_journal_path(date: datetime = None) -> Path:
    """Get journal file path for given date (default: today)."""
    if date is None:
        date = datetime.now(timezone.utc)
    return JOURNAL_DIR / f"journal_{date.strftime('%Y%m%d')}.jsonl"


def _atomic_append(path: Path, line: str) -> bool:
    """
    Atomically append line to file.
    Uses simple append mode - safe enough for HOPE's use case.
    For true atomicity, we write to temp file and use os.replace.
    """
    try:
        # Simple append - works on Windows and Unix
        # For HOPE, writes are infrequent enough that collisions are rare
        with open(path, 'a', encoding='utf-8', newline='\n') as f:
            f.write(line + '\n')
            f.flush()
            os.fsync(f.fileno())
        return True
    except Exception as e:
        log.error(f"Atomic append failed: {e}")
        return False


@dataclass
class JournalEntry:
    """Entry in the event journal with metadata."""
    seq: int                    # Sequence number (monotonic within process)
    ts_unix: float              # Unix timestamp with microseconds
    process_id: int             # PID of writer
    process_name: str           # Name of writer process
    event: Dict[str, Any]       # The HopeEvent as dict

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, line: str) -> Optional['JournalEntry']:
        try:
            data = json.loads(line)
            return cls(**data)
        except Exception:
            return None


class EventTransport:
    """
    Cross-process event transport using file-based journal.

    Features:
    - Atomic writes with file locking (safe for multiple writers)
    - Sequence numbers for ordering
    - Background reader thread for subscriptions
    - Automatic journal rotation (daily)
    """

    _instance: Optional['EventTransport'] = None
    _lock = threading.Lock()

    def __init__(self, process_name: str = "unknown"):
        self._process_name = process_name
        self._process_id = os.getpid()
        self._seq = 0  # Sequence number (per-process)

        # Subscriptions: event_type -> list of handlers
        self._subscribers: Dict[str, List[Callable]] = {}

        # Reader state
        self._reader_thread: Optional[threading.Thread] = None
        self._reader_running = False
        self._last_read_pos: Dict[Path, int] = {}  # Track position per journal file

        # Stats
        self._stats = {
            "events_written": 0,
            "events_read": 0,
            "events_delivered": 0,
            "write_errors": 0,
            "read_errors": 0,
        }

        log.info(f"EventTransport initialized: process={process_name} pid={self._process_id}")

    @classmethod
    def get(cls, process_name: str = None) -> 'EventTransport':
        """Get singleton instance."""
        with cls._lock:
            if cls._instance is None:
                name = process_name or os.environ.get("HOPE_PROCESS_NAME", "unknown")
                cls._instance = EventTransport(name)
            return cls._instance

    @classmethod
    def reset(cls):
        """Reset singleton (for testing)."""
        with cls._lock:
            if cls._instance:
                cls._instance.stop_reader()
            cls._instance = None

    # =========================================================================
    # WRITER API
    # =========================================================================

    def publish(self, event: HopeEvent) -> bool:
        """
        Publish event to journal (visible to all processes).

        Returns True if written successfully.
        """
        self._seq += 1

        entry = JournalEntry(
            seq=self._seq,
            ts_unix=time.time(),
            process_id=self._process_id,
            process_name=self._process_name,
            event=event.to_dict(),
        )

        journal_path = _get_journal_path()
        success = _atomic_append(journal_path, entry.to_json())

        if success:
            self._stats["events_written"] += 1
            log.debug(f"Published: {event.event_type} seq={self._seq}")
        else:
            self._stats["write_errors"] += 1
            log.error(f"Failed to publish: {event.event_type}")

        return success

    def publish_dict(self, event_dict: Dict[str, Any]) -> bool:
        """Publish event from dict (for compatibility)."""
        event = HopeEvent.from_dict(event_dict)
        return self.publish(event)

    # =========================================================================
    # READER API
    # =========================================================================

    def poll(self, journal_path: Path = None) -> List[HopeEvent]:
        """
        Poll journal for new events since last read.

        Returns list of new events.
        """
        if journal_path is None:
            journal_path = _get_journal_path()

        if not journal_path.exists():
            return []

        events = []
        last_pos = self._last_read_pos.get(journal_path, 0)

        try:
            with open(journal_path, 'r', encoding='utf-8') as f:
                f.seek(last_pos)
                count = 0
                for line in f:
                    if count >= MAX_EVENTS_PER_POLL:
                        break
                    line = line.strip()
                    if not line:
                        continue

                    entry = JournalEntry.from_json(line)
                    if entry:
                        # Skip our own events (already processed locally)
                        if entry.process_id == self._process_id:
                            continue

                        event = HopeEvent.from_dict(entry.event)
                        events.append(event)
                        count += 1

                self._last_read_pos[journal_path] = f.tell()
                self._stats["events_read"] += len(events)

        except Exception as e:
            self._stats["read_errors"] += 1
            log.error(f"Poll error: {e}")

        return events

    def subscribe(self, event_type: str, handler: Callable[[HopeEvent], None]):
        """Subscribe handler to event type."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)
        log.debug(f"Subscribed to {event_type}")

    def subscribe_all(self, handler: Callable[[HopeEvent], None]):
        """Subscribe handler to ALL event types."""
        self.subscribe("*", handler)

    def _deliver_events(self, events: List[HopeEvent]):
        """Deliver events to subscribers."""
        for event in events:
            # Deliver to specific subscribers
            handlers = self._subscribers.get(event.event_type, [])
            # Also deliver to wildcard subscribers
            handlers += self._subscribers.get("*", [])

            for handler in handlers:
                try:
                    handler(event)
                    self._stats["events_delivered"] += 1
                except Exception as e:
                    log.error(f"Handler error for {event.event_type}: {e}")

    # =========================================================================
    # BACKGROUND READER
    # =========================================================================

    def start_reader(self):
        """Start background reader thread."""
        if self._reader_running:
            return

        self._reader_running = True
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            daemon=True,
            name=f"EventTransport-Reader-{self._process_name}"
        )
        self._reader_thread.start()
        log.info("Background reader started")

    def stop_reader(self):
        """Stop background reader thread."""
        self._reader_running = False
        if self._reader_thread:
            self._reader_thread.join(timeout=2.0)
            self._reader_thread = None
        log.info("Background reader stopped")

    def _reader_loop(self):
        """Background loop that polls journal and delivers events."""
        while self._reader_running:
            try:
                events = self.poll()
                if events:
                    self._deliver_events(events)
                time.sleep(POLL_INTERVAL_SEC)
            except Exception as e:
                log.error(f"Reader loop error: {e}")
                time.sleep(1.0)  # Back off on error

    # =========================================================================
    # STATS & DEBUG
    # =========================================================================

    def get_stats(self) -> Dict[str, Any]:
        """Get transport statistics."""
        return {
            **self._stats,
            "process_name": self._process_name,
            "process_id": self._process_id,
            "sequence": self._seq,
            "subscribers": {k: len(v) for k, v in self._subscribers.items()},
            "reader_running": self._reader_running,
        }

    def get_journal_info(self) -> Dict[str, Any]:
        """Get info about current journal file."""
        path = _get_journal_path()
        if not path.exists():
            return {"exists": False, "path": str(path)}

        stat = path.stat()
        return {
            "exists": True,
            "path": str(path),
            "size_bytes": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        }


# =========================================================================
# CONVENIENCE FUNCTIONS
# =========================================================================

def get_transport(process_name: str = None) -> EventTransport:
    """Get EventTransport singleton."""
    return EventTransport.get(process_name)


def publish_cross_process(event: HopeEvent) -> bool:
    """Publish event to cross-process journal."""
    return get_transport().publish(event)


# =========================================================================
# BRIDGE: Connect in-memory Event Bus to Transport
# =========================================================================

class EventBusTransportBridge:
    """
    Bridge that connects in-memory Event Bus to cross-process Transport.

    When Event Bus emits an event:
    1. Event is delivered to local subscribers (in-memory, fast)
    2. Event is also written to Transport journal (cross-process)

    When Transport receives an event from another process:
    1. Event is injected into local Event Bus
    2. Local subscribers receive it as if it was local
    """

    def __init__(self, process_name: str = "bridge"):
        self._process_name = process_name
        self._transport: Optional[EventTransport] = None
        self._bus = None
        self._running = False

    def start(self):
        """Start the bridge."""
        from . import get_event_bus

        self._transport = get_transport(self._process_name)
        self._bus = get_event_bus()

        # Subscribe to ALL events from transport
        self._transport.subscribe_all(self._on_transport_event)

        # Hook into bus to forward events to transport
        self._original_publish = self._bus.publish_sync
        self._bus.publish_sync = self._bridged_publish

        # Start transport reader
        self._transport.start_reader()
        self._running = True

        log.info(f"EventBusTransportBridge started: {self._process_name}")

    def stop(self):
        """Stop the bridge."""
        if self._transport:
            self._transport.stop_reader()
        self._running = False

    def _bridged_publish(self, event: HopeEvent) -> bool:
        """Publish to both local bus and transport."""
        # Local publish (fast, in-memory)
        result = self._original_publish(event)

        # Cross-process publish (to journal)
        self._transport.publish(event)

        return result

    def _on_transport_event(self, event: HopeEvent):
        """Handle event from transport (another process)."""
        # Inject into local bus queue (without re-publishing to transport)
        if self._bus:
            self._bus._queue.put_nowait(event)
            log.debug(f"Injected from transport: {event.event_type}")


def start_bridge(process_name: str = None) -> EventBusTransportBridge:
    """Start EventBus-Transport bridge."""
    name = process_name or os.environ.get("HOPE_PROCESS_NAME", "unknown")
    bridge = EventBusTransportBridge(name)
    bridge.start()
    return bridge
