"""
HOPE/NORE Event Journal v1.0

Atomic append-only JSONL journal with cursor/ack mechanism.
Ensures reliable, idempotent event delivery.

Design principles:
- Append-only: events are never modified after write
- Atomic writes: temp → fsync → rename pattern
- Cursor-based: track last processed event per consumer
- Dead-letter: failed events stored separately
- sha256 prefix for self-documenting format

File format:
- events.jsonl: append-only event log
- cursors.json: {consumer_id: last_processed_event_id}
- deadletter.jsonl: events that failed delivery

Usage:
    from core.event_journal import EventJournal

    journal = EventJournal()

    # Append event
    journal.append(event)

    # Get unprocessed events for consumer
    events = journal.get_pending("telegram")

    # Acknowledge processed event
    journal.ack("telegram", event.event_id)
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional, Set

from core.event_contract import Event

logger = logging.getLogger(__name__)

# Default paths
STATE_DIR = Path(r"C:\Users\kirillDev\Desktop\TradingBot\minibot\state")
EVENTS_FILE = STATE_DIR / "events.jsonl"
CURSORS_FILE = STATE_DIR / "cursors.json"
DEADLETTER_FILE = STATE_DIR / "deadletter.jsonl"

# Limits
MAX_EVENTS_IN_MEMORY = 1000
MAX_PENDING_PER_CONSUMER = 100


@dataclass
class JournalStats:
    """Journal statistics."""
    total_events: int
    pending_by_consumer: Dict[str, int]
    deadletter_count: int
    last_event_ts: float


class EventJournal:
    """
    Atomic append-only event journal with cursor tracking.

    Thread-safe via internal lock.
    """

    def __init__(
        self,
        events_path: Path = EVENTS_FILE,
        cursors_path: Path = CURSORS_FILE,
        deadletter_path: Path = DEADLETTER_FILE,
    ):
        self._events_path = events_path
        self._cursors_path = cursors_path
        self._deadletter_path = deadletter_path
        self._lock = Lock()

        # In-memory cache
        self._events: List[Event] = []
        self._event_ids: Set[str] = set()
        self._cursors: Dict[str, str] = {}  # consumer_id -> last_acked_event_id

        # Ensure directories exist
        self._events_path.parent.mkdir(parents=True, exist_ok=True)

        # Load existing data
        self._load_events()
        self._load_cursors()

    def _load_events(self) -> None:
        """Load events from JSONL file."""
        if not self._events_path.exists():
            return

        try:
            with open(self._events_path, "r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        event = Event.from_dict(data)
                        if event.event_id not in self._event_ids:
                            self._events.append(event)
                            self._event_ids.add(event.event_id)
                    except (json.JSONDecodeError, KeyError) as e:
                        logger.warning("Skipping malformed event at line %d: %s", line_no, e)

            # Keep only recent events in memory
            if len(self._events) > MAX_EVENTS_IN_MEMORY:
                self._events = self._events[-MAX_EVENTS_IN_MEMORY:]
                self._event_ids = {e.event_id for e in self._events}

            logger.info("Loaded %d events from journal", len(self._events))

        except OSError as e:
            logger.error("Failed to load events: %s", e)

    def _load_cursors(self) -> None:
        """Load cursor positions from JSON file."""
        if not self._cursors_path.exists():
            return

        try:
            content = self._cursors_path.read_text(encoding="utf-8")
            self._cursors = json.loads(content)
            logger.info("Loaded cursors for %d consumers", len(self._cursors))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load cursors: %s", e)
            self._cursors = {}

    def _save_cursors(self) -> None:
        """Save cursor positions atomically (temp → fsync → rename)."""
        temp_path = self._cursors_path.with_suffix(".tmp")
        try:
            content = json.dumps(self._cursors, ensure_ascii=False, indent=2)

            # Write to temp file
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())

            # Atomic rename
            temp_path.replace(self._cursors_path)

        except OSError as e:
            logger.error("Failed to save cursors: %s", e)
            # Clean up temp file
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _append_to_file(self, path: Path, event: Event) -> bool:
        """Append event to JSONL file atomically."""
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(event.to_json() + "\n")
                f.flush()
                os.fsync(f.fileno())
            return True
        except OSError as e:
            logger.error("Failed to append to %s: %s", path, e)
            return False

    def append(self, event: Event) -> bool:
        """
        Append event to journal.

        Idempotent: duplicate event_ids are ignored.

        Args:
            event: Event to append

        Returns:
            True if appended, False if duplicate or error
        """
        with self._lock:
            # Check for duplicate
            if event.event_id in self._event_ids:
                logger.debug("Skipping duplicate event: %s", event.event_id)
                return False

            # Append to file
            if not self._append_to_file(self._events_path, event):
                return False

            # Update in-memory cache
            self._events.append(event)
            self._event_ids.add(event.event_id)

            # Trim cache if needed
            if len(self._events) > MAX_EVENTS_IN_MEMORY:
                removed = self._events.pop(0)
                self._event_ids.discard(removed.event_id)

            logger.debug("Appended event: %s", event.event_id)
            return True

    def append_batch(self, events: List[Event]) -> int:
        """
        Append multiple events.

        Returns: Number of events successfully appended
        """
        count = 0
        for event in events:
            if self.append(event):
                count += 1
        return count

    def get_pending(
        self,
        consumer_id: str,
        limit: int = MAX_PENDING_PER_CONSUMER,
    ) -> List[Event]:
        """
        Get events pending for a consumer (not yet acked).

        Args:
            consumer_id: Unique consumer identifier
            limit: Maximum events to return

        Returns:
            List of unprocessed events, oldest first
        """
        with self._lock:
            last_acked = self._cursors.get(consumer_id)

            if last_acked is None:
                # Consumer is new, return all events
                return self._events[:limit]

            # Find position after last acked
            pending = []
            found_cursor = False

            for event in self._events:
                if found_cursor:
                    pending.append(event)
                    if len(pending) >= limit:
                        break
                elif event.event_id == last_acked:
                    found_cursor = True

            # If cursor not found (old event pruned), return recent events
            if not found_cursor and last_acked:
                logger.warning(
                    "Cursor %s not found for consumer %s, returning recent events",
                    last_acked, consumer_id
                )
                return self._events[-limit:]

            return pending

    def ack(self, consumer_id: str, event_id: str) -> bool:
        """
        Acknowledge event as processed by consumer.

        Args:
            consumer_id: Consumer identifier
            event_id: Event ID to acknowledge

        Returns:
            True if acknowledged, False if event not found
        """
        with self._lock:
            # Verify event exists
            if event_id not in self._event_ids:
                logger.warning("Cannot ack unknown event: %s", event_id)
                return False

            # Update cursor
            self._cursors[consumer_id] = event_id
            self._save_cursors()

            logger.debug("Consumer %s acked event %s", consumer_id, event_id)
            return True

    def ack_batch(self, consumer_id: str, event_ids: List[str]) -> int:
        """
        Acknowledge multiple events.

        Args:
            consumer_id: Consumer identifier
            event_ids: List of event IDs to acknowledge

        Returns:
            Number of events acknowledged (last one becomes cursor)
        """
        if not event_ids:
            return 0

        count = 0
        for event_id in event_ids:
            if event_id in self._event_ids:
                count += 1

        if count > 0:
            # Set cursor to last event
            self.ack(consumer_id, event_ids[-1])

        return count

    def send_to_deadletter(self, event: Event, error: str) -> bool:
        """
        Move failed event to dead-letter queue.

        Args:
            event: Failed event
            error: Error message

        Returns:
            True if saved to deadletter
        """
        with self._lock:
            # Add error metadata
            dl_data = event.to_dict()
            dl_data["_deadletter"] = {
                "error": error,
                "timestamp": time.time(),
            }

            try:
                with open(self._deadletter_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(dl_data, ensure_ascii=False) + "\n")
                    f.flush()
                    os.fsync(f.fileno())

                logger.warning("Event %s sent to deadletter: %s", event.event_id, error)
                return True

            except OSError as e:
                logger.error("Failed to write deadletter: %s", e)
                return False

    def get_stats(self) -> JournalStats:
        """Get journal statistics."""
        with self._lock:
            # Count deadletter entries
            dl_count = 0
            if self._deadletter_path.exists():
                try:
                    with open(self._deadletter_path, "r", encoding="utf-8") as f:
                        dl_count = sum(1 for _ in f)
                except OSError:
                    pass

            # Calculate pending per consumer
            pending_counts = {}
            for consumer_id in self._cursors:
                pending = self.get_pending(consumer_id, limit=1000)
                pending_counts[consumer_id] = len(pending)

            return JournalStats(
                total_events=len(self._events),
                pending_by_consumer=pending_counts,
                deadletter_count=dl_count,
                last_event_ts=self._events[-1].timestamp_unix if self._events else 0,
            )

    def has_event(self, event_id: str) -> bool:
        """Check if event exists in journal."""
        with self._lock:
            return event_id in self._event_ids

    def get_event(self, event_id: str) -> Optional[Event]:
        """Get event by ID."""
        with self._lock:
            for event in self._events:
                if event.event_id == event_id:
                    return event
            return None

    def clear_consumer(self, consumer_id: str) -> None:
        """Reset cursor for consumer (reprocess all events)."""
        with self._lock:
            if consumer_id in self._cursors:
                del self._cursors[consumer_id]
                self._save_cursors()
                logger.info("Cleared cursor for consumer: %s", consumer_id)


def get_event_journal() -> EventJournal:
    """Get singleton journal instance."""
    global _journal_instance
    if "_journal_instance" not in globals():
        _journal_instance = EventJournal()
    return _journal_instance


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    from core.event_contract import create_event, EventType

    print("=== EVENT JOURNAL TEST ===\n")

    journal = EventJournal()

    # Create test events
    events = [
        create_event(
            EventType.REGULATION,
            "SEC Meeting Scheduled",
            "reuters",
            impact_score=0.7,
        ),
        create_event(
            EventType.MARKET,
            "BTC Breaks $100K",
            "binance",
            impact_score=0.9,
            assets=["BTC"],
        ),
        create_event(
            EventType.EXPLOIT,
            "DeFi Protocol Hacked",
            "coindesk",
            impact_score=0.85,
        ),
    ]

    # Append events
    print("Appending events...")
    for event in events:
        result = journal.append(event)
        print(f"  {event.event_id}: {'OK' if result else 'SKIP (duplicate)'}")

    # Test idempotency
    print("\nTesting idempotency (re-append same events)...")
    for event in events:
        result = journal.append(event)
        print(f"  {event.event_id}: {'OK' if result else 'SKIP (duplicate)'}")

    # Get pending for consumer
    print("\nPending events for 'telegram':")
    pending = journal.get_pending("telegram")
    for event in pending:
        print(f"  {event.event_id}: {event.title[:40]}...")

    # Ack first event
    if pending:
        print(f"\nAcking first event: {pending[0].event_id}")
        journal.ack("telegram", pending[0].event_id)

        print("\nPending after ack:")
        pending2 = journal.get_pending("telegram")
        for event in pending2:
            print(f"  {event.event_id}: {event.title[:40]}...")

    # Stats
    print("\nJournal stats:")
    stats = journal.get_stats()
    print(f"  Total events: {stats.total_events}")
    print(f"  Deadletter: {stats.deadletter_count}")
    print(f"  Pending by consumer: {stats.pending_by_consumer}")
