# === AI SIGNATURE ===
# Module: hope_core/journal/event_journal.py
# Created by: Claude (opus-4.5)
# Created at: 2026-02-04 10:00:00 UTC
# Purpose: Append-only event journal with hash chain for integrity
# === END SIGNATURE ===
"""
HOPE Core - Event Journal

Append-only log of all events with hash chain for integrity.
Enables replay, audit, and recovery.

Features:
- Hash chain for tamper detection
- Correlation IDs for event linking
- Automatic rotation
- Replay capability
"""

from typing import Dict, List, Optional, Any, Generator
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import threading
import hashlib
import json
import os


# =============================================================================
# EVENT TYPES
# =============================================================================

class EventType(Enum):
    """All event types in the system."""
    
    # State events
    STATE_CHANGE = "STATE_CHANGE"
    CHECKPOINT = "CHECKPOINT"
    ROLLBACK = "ROLLBACK"
    
    # Command events
    COMMAND_RECEIVED = "COMMAND_RECEIVED"
    COMMAND_VALIDATED = "COMMAND_VALIDATED"
    COMMAND_REJECTED = "COMMAND_REJECTED"
    COMMAND_EXECUTED = "COMMAND_EXECUTED"
    COMMAND_FAILED = "COMMAND_FAILED"
    
    # Trading events
    SIGNAL_RECEIVED = "SIGNAL_RECEIVED"
    DECISION_MADE = "DECISION_MADE"
    ORDER_SENT = "ORDER_SENT"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_FAILED = "ORDER_FAILED"
    POSITION_OPENED = "POSITION_OPENED"
    POSITION_CLOSED = "POSITION_CLOSED"
    
    # System events
    HEARTBEAT = "HEARTBEAT"
    STARTUP = "STARTUP"
    SHUTDOWN = "SHUTDOWN"
    SYNC = "SYNC"
    
    # Error events
    ERROR = "ERROR"
    ALERT = "ALERT"
    EMERGENCY_STOP = "EMERGENCY_STOP"


class EventLevel(Enum):
    """Event severity levels."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


# =============================================================================
# EVENT DATACLASS
# =============================================================================

@dataclass
class Event:
    """
    Single event in the journal.
    
    Immutable after creation.
    """
    id: str                     # Unique event ID
    timestamp: datetime         # Event timestamp (UTC)
    event_type: EventType       # Type of event
    level: EventLevel           # Severity level
    correlation_id: str         # Links related events
    payload: Dict[str, Any]     # Event-specific data
    hash: str = ""              # Hash (computed after creation)
    previous_hash: str = ""     # Hash of previous event
    
    # Optional context
    from_state: Optional[str] = None   # For state changes
    to_state: Optional[str] = None     # For state changes
    command_type: Optional[str] = None # For command events
    symbol: Optional[str] = None       # For trading events
    order_id: Optional[str] = None     # For order events
    position_id: Optional[str] = None  # For position events
    
    def compute_hash(self, previous_hash: str = "") -> str:
        """Compute hash of this event with previous hash."""
        data = {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type.value,
            "correlation_id": self.correlation_id,
            "payload": self.payload,
            "previous_hash": previous_hash,
        }
        content = json.dumps(data, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type.value,
            "level": self.level.value,
            "correlation_id": self.correlation_id,
            "payload": self.payload,
            "hash": self.hash,
            "previous_hash": self.previous_hash,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "command_type": self.command_type,
            "symbol": self.symbol,
            "order_id": self.order_id,
            "position_id": self.position_id,
        }
    
    def to_jsonl(self) -> str:
        """Serialize to JSONL line."""
        return json.dumps(self.to_dict(), separators=(",", ":"))
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
        """Deserialize from dictionary."""
        return cls(
            id=data["id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            event_type=EventType(data["event_type"]),
            level=EventLevel(data["level"]),
            correlation_id=data["correlation_id"],
            payload=data.get("payload", {}),
            hash=data.get("hash", ""),
            previous_hash=data.get("previous_hash", ""),
            from_state=data.get("from_state"),
            to_state=data.get("to_state"),
            command_type=data.get("command_type"),
            symbol=data.get("symbol"),
            order_id=data.get("order_id"),
            position_id=data.get("position_id"),
        )
    
    @classmethod
    def from_jsonl(cls, line: str) -> "Event":
        """Deserialize from JSONL line."""
        return cls.from_dict(json.loads(line))


# =============================================================================
# EVENT JOURNAL
# =============================================================================

class EventJournal:
    """
    Append-only event journal with hash chain.
    
    Features:
    - Thread-safe append
    - Hash chain for integrity verification
    - File rotation
    - Replay from journal
    """
    
    def __init__(
        self,
        journal_path: Path,
        max_size_mb: int = 100,
        auto_rotate: bool = True,
    ):
        """
        Initialize Event Journal.
        
        Args:
            journal_path: Path to journal file (.jsonl)
            max_size_mb: Max file size before rotation (MB)
            auto_rotate: Enable automatic rotation
        """
        self._path = Path(journal_path)
        self._max_size = max_size_mb * 1024 * 1024  # Convert to bytes
        self._auto_rotate = auto_rotate
        
        self._lock = threading.RLock()
        self._last_hash = ""
        self._event_count = 0
        
        # Ensure directory exists
        self._path.parent.mkdir(parents=True, exist_ok=True)
        
        # Load last hash from existing journal
        self._load_last_hash()
    
    def _load_last_hash(self):
        """Load last hash from existing journal."""
        if self._path.exists() and self._path.stat().st_size > 0:
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    last_line = None
                    for line in f:
                        if line.strip():
                            last_line = line
                            self._event_count += 1
                    
                    if last_line:
                        event = Event.from_jsonl(last_line)
                        self._last_hash = event.hash
            except Exception as e:
                print(f"WARNING: Could not load journal: {e}")
    
    def _generate_event_id(self) -> str:
        """Generate unique event ID."""
        import uuid
        return f"evt_{uuid.uuid4().hex[:12]}"
    
    def append(
        self,
        event_type: EventType | str,
        payload: Dict[str, Any],
        correlation_id: str,
        level: EventLevel = EventLevel.INFO,
        **kwargs,
    ) -> Event:
        """
        Append event to journal.
        
        Args:
            event_type: Type of event (EventType enum or string)
            payload: Event-specific data
            correlation_id: Links related events
            level: Severity level
            **kwargs: Additional event fields (symbol, order_id, etc.)
            
        Returns:
            Created Event
        """
        with self._lock:
            # Convert string to EventType if needed
            if isinstance(event_type, str):
                try:
                    event_type = EventType(event_type)
                except ValueError:
                    # Create custom event type by using ERROR as fallback
                    # but keep original name in payload
                    payload["_original_event_type"] = event_type
                    event_type = EventType.ERROR
            
            # Create event
            event = Event(
                id=self._generate_event_id(),
                timestamp=datetime.now(timezone.utc),
                event_type=event_type,
                level=level,
                correlation_id=correlation_id,
                payload=payload,
                previous_hash=self._last_hash,
            )
            
            # Compute hash
            event.hash = event.compute_hash(self._last_hash)
            self._last_hash = event.hash
            self._event_count += 1
            
            # Write to file
            self._write_event(event)
            
            # Check rotation
            if self._auto_rotate:
                self._check_rotation()
            
            return event
    
    def _write_event(self, event: Event):
        """Write event to journal file."""
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(event.to_jsonl() + "\n")
            f.flush()
            os.fsync(f.fileno())  # Ensure durability
    
    def _check_rotation(self):
        """Check if rotation is needed."""
        if self._path.exists() and self._path.stat().st_size > self._max_size:
            self._rotate()
    
    def _rotate(self):
        """Rotate journal file."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        rotated_path = self._path.with_suffix(f".{timestamp}.jsonl")
        self._path.rename(rotated_path)
        self._event_count = 0
    
    def verify_integrity(self) -> tuple[bool, List[str]]:
        """
        Verify hash chain integrity.
        
        Returns:
            Tuple of (is_valid, list of errors)
        """
        errors = []
        previous_hash = ""
        
        if not self._path.exists():
            return True, []
        
        with open(self._path, "r", encoding="utf-8") as f:
            line_num = 0
            for line in f:
                line_num += 1
                if not line.strip():
                    continue
                
                try:
                    event = Event.from_jsonl(line)
                    
                    # Check previous hash
                    if event.previous_hash != previous_hash:
                        errors.append(
                            f"Line {line_num}: previous_hash mismatch "
                            f"(expected: {previous_hash[:8]}, got: {event.previous_hash[:8]})"
                        )
                    
                    # Verify event hash
                    computed = event.compute_hash(previous_hash)
                    if event.hash != computed:
                        errors.append(
                            f"Line {line_num}: hash mismatch "
                            f"(expected: {computed[:8]}, got: {event.hash[:8]})"
                        )
                    
                    previous_hash = event.hash
                    
                except Exception as e:
                    errors.append(f"Line {line_num}: parse error - {e}")
        
        return len(errors) == 0, errors
    
    def read_events(
        self,
        event_types: Optional[List[EventType]] = None,
        correlation_id: Optional[str] = None,
        after: Optional[datetime] = None,
        before: Optional[datetime] = None,
        limit: int = 1000,
    ) -> Generator[Event, None, None]:
        """
        Read events from journal with filters.
        
        Args:
            event_types: Filter by event types
            correlation_id: Filter by correlation ID
            after: Events after this time
            before: Events before this time
            limit: Maximum events to return
            
        Yields:
            Matching events
        """
        if not self._path.exists():
            return
        
        count = 0
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                if count >= limit:
                    break
                
                if not line.strip():
                    continue
                
                try:
                    event = Event.from_jsonl(line)
                    
                    # Apply filters
                    if event_types and event.event_type not in event_types:
                        continue
                    if correlation_id and event.correlation_id != correlation_id:
                        continue
                    if after and event.timestamp < after:
                        continue
                    if before and event.timestamp > before:
                        continue
                    
                    yield event
                    count += 1
                    
                except Exception:
                    continue
    
    def get_events_by_correlation(self, correlation_id: str) -> List[Event]:
        """Get all events for a correlation ID."""
        return list(self.read_events(correlation_id=correlation_id, limit=10000))
    
    def get_latest_events(self, n: int = 100) -> List[Event]:
        """Get latest N events."""
        events = list(self.read_events(limit=100000))
        return events[-n:]
    
    @property
    def event_count(self) -> int:
        """Get total event count."""
        return self._event_count
    
    @property
    def last_hash(self) -> str:
        """Get last event hash."""
        return self._last_hash
    
    def get_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get recent events as dictionaries.
        
        Args:
            limit: Maximum events to return
            
        Returns:
            List of event dictionaries
        """
        events = self.get_latest_events(limit)
        return [e.to_dict() for e in events]
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get journal statistics.
        
        Returns:
            Statistics dictionary
        """
        stats = {
            "event_count": self._event_count,
            "last_hash": self._last_hash[:16] if self._last_hash else None,
            "path": str(self._path),
            "size_bytes": self._path.stat().st_size if self._path.exists() else 0,
        }
        
        # Count by event type
        type_counts: Dict[str, int] = {}
        for event in self.read_events(limit=10000):
            type_name = event.event_type.value if hasattr(event.event_type, 'value') else str(event.event_type)
            type_counts[type_name] = type_counts.get(type_name, 0) + 1
        
        stats["by_type"] = type_counts
        
        return stats


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def create_state_change_event(
    journal: EventJournal,
    correlation_id: str,
    from_state: str,
    to_state: str,
    reason: str,
    **kwargs,
) -> Event:
    """Create and append state change event."""
    return journal.append(
        event_type=EventType.STATE_CHANGE,
        payload={"reason": reason, **kwargs},
        correlation_id=correlation_id,
        from_state=from_state,
        to_state=to_state,
    )


def create_order_event(
    journal: EventJournal,
    event_type: EventType,
    correlation_id: str,
    symbol: str,
    order_id: str,
    payload: Dict[str, Any],
    **kwargs,
) -> Event:
    """Create and append order-related event."""
    return journal.append(
        event_type=event_type,
        payload=payload,
        correlation_id=correlation_id,
        symbol=symbol,
        order_id=order_id,
        **kwargs,
    )


def create_heartbeat_event(
    journal: EventJournal,
    state: str,
    memory_mb: float,
    open_positions: int,
) -> Event:
    """Create and append heartbeat event."""
    return journal.append(
        event_type=EventType.HEARTBEAT,
        payload={
            "state": state,
            "memory_mb": memory_mb,
            "open_positions": open_positions,
        },
        correlation_id="heartbeat",
        level=EventLevel.DEBUG,
    )


# =============================================================================
# TESTING
# =============================================================================

if __name__ == "__main__":
    import tempfile
    
    print("=== Event Journal Tests ===\n")
    
    # Create temporary journal
    with tempfile.TemporaryDirectory() as tmpdir:
        journal_path = Path(tmpdir) / "test_journal.jsonl"
        journal = EventJournal(journal_path)
        
        # Test 1: Append events
        print("Test 1: Append events")
        corr_id = "test_corr_001"
        
        e1 = journal.append(
            EventType.STARTUP,
            {"version": "2.0"},
            corr_id,
        )
        print(f"  Event 1: {e1.id} (hash: {e1.hash})")
        
        e2 = journal.append(
            EventType.STATE_CHANGE,
            {"reason": "Start scanning"},
            corr_id,
            from_state="IDLE",
            to_state="SCANNING",
        )
        print(f"  Event 2: {e2.id} (hash: {e2.hash})")
        
        e3 = journal.append(
            EventType.SIGNAL_RECEIVED,
            {"score": 75, "source": "MOMENTUM"},
            corr_id,
            symbol="BTCUSDT",
        )
        print(f"  Event 3: {e3.id} (hash: {e3.hash})")
        print(f"  Total events: {journal.event_count}")
        print()
        
        # Test 2: Hash chain
        print("Test 2: Hash chain verification")
        print(f"  Event 1 previous_hash: '{e1.previous_hash}'")
        print(f"  Event 2 previous_hash: '{e2.previous_hash[:8]}...'")
        print(f"  Event 3 previous_hash: '{e3.previous_hash[:8]}...'")
        print(f"  Chain matches: {e2.previous_hash == e1.hash and e3.previous_hash == e2.hash}")
        print()
        
        # Test 3: Verify integrity
        print("Test 3: Verify integrity")
        valid, errors = journal.verify_integrity()
        print(f"  Valid: {valid}")
        print(f"  Errors: {errors}")
        print()
        
        # Test 4: Read events with filters
        print("Test 4: Read events with filters")
        events = list(journal.read_events(
            event_types=[EventType.STATE_CHANGE]
        ))
        print(f"  STATE_CHANGE events: {len(events)}")
        
        events = list(journal.read_events(correlation_id=corr_id))
        print(f"  Events with correlation {corr_id}: {len(events)}")
        print()
        
        # Test 5: Heartbeat
        print("Test 5: Heartbeat event")
        hb = create_heartbeat_event(journal, "IDLE", 150.5, 0)
        print(f"  Heartbeat: {hb.id}")
        print(f"  Payload: {hb.payload}")
        print()
        
        # Test 6: Reload and verify
        print("Test 6: Reload journal and verify")
        journal2 = EventJournal(journal_path)
        print(f"  Loaded event count: {journal2.event_count}")
        print(f"  Last hash matches: {journal2.last_hash == journal.last_hash}")
        valid2, errors2 = journal2.verify_integrity()
        print(f"  Integrity valid: {valid2}")
        print()
        
        print("=== All Tests Completed ===")
