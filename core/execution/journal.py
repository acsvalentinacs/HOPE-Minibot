# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T00:30:00Z
# Purpose: Atomic append-only JSONL journal for execution audit trail
# Security: fsync on every write, corruption detection, fail-closed
# === END SIGNATURE ===
"""
Atomic Journal - Append-only JSONL with fsync.

CRITICAL: Every write is:
1. Append to file
2. fsync to disk
3. Verified before returning

This ensures durability even on crash. No data loss.
"""
import os
import sys
import json
import hashlib
from pathlib import Path
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Iterator, Callable
from contextlib import contextmanager

# Platform-specific locking
if sys.platform == "win32":
    import msvcrt
    _LOCK_EX = msvcrt.LK_NBLCK  # Non-blocking exclusive lock
    _LOCK_UN = None  # Unlock is implicit via lseek + unlock
else:
    import fcntl
    _LOCK_EX = fcntl.LOCK_EX
    _LOCK_UN = fcntl.LOCK_UN


@dataclass
class JournalEntry:
    """Single journal entry."""
    entry_type: str  # "intent", "ack", "fill", "error", etc.
    data: Dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    entry_id: str = ""  # SHA256 of content
    sequence: int = 0  # Monotonic sequence number

    def __post_init__(self):
        """Generate entry_id if not provided."""
        if not self.entry_id:
            content = json.dumps(
                {"t": self.entry_type, "d": self.data, "ts": self.timestamp},
                sort_keys=True,
                separators=(",", ":"),
            )
            self.entry_id = hashlib.sha256(content.encode()).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return asdict(self)

    def to_json(self) -> str:
        """Serialize to single-line JSON."""
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JournalEntry":
        """Deserialize from dict."""
        return cls(**data)

    @classmethod
    def from_json(cls, line: str) -> "JournalEntry":
        """Deserialize from JSON line."""
        return cls.from_dict(json.loads(line))


class AtomicJournal:
    """
    Atomic append-only JSONL journal.

    Features:
    - Every write is fsync'd to disk
    - File locking prevents concurrent corruption
    - Monotonic sequence numbers
    - Corruption detection on read

    Usage:
        journal = AtomicJournal(Path("state/orders.jsonl"))
        journal.append("intent", {"symbol": "BTCUSDT", ...})
    """

    def __init__(self, path: Path, create_dirs: bool = True):
        """
        Initialize journal.

        Args:
            path: Path to JSONL file
            create_dirs: Create parent directories if missing
        """
        self.path = path
        if create_dirs:
            path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize sequence from existing entries
        self._sequence = self._count_entries()

    def _count_entries(self) -> int:
        """Count existing entries to initialize sequence."""
        if not self.path.exists():
            return 0
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return sum(1 for line in f if line.strip())
        except Exception:
            return 0

    @contextmanager
    def _locked_append(self):
        """Context manager for locked file append (cross-platform)."""
        # Open in append mode, create if needed
        fd = os.open(
            str(self.path),
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o644,
        )
        try:
            if sys.platform == "win32":
                # Windows: use msvcrt locking
                # Lock first byte (convention for append-only)
                max_retries = 50
                for attempt in range(max_retries):
                    try:
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if attempt == max_retries - 1:
                            raise
                        import time
                        time.sleep(0.1)
                try:
                    yield fd
                finally:
                    os.fsync(fd)
                    # Seek to start to unlock
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                # Unix: use fcntl locking
                fcntl.flock(fd, fcntl.LOCK_EX)
                try:
                    yield fd
                finally:
                    os.fsync(fd)
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def append(
        self,
        entry_type: str,
        data: Dict[str, Any],
        timestamp: Optional[str] = None,
    ) -> JournalEntry:
        """
        Append entry to journal with fsync.

        Args:
            entry_type: Type of entry (e.g., "intent", "ack", "fill")
            data: Entry payload
            timestamp: Optional timestamp (defaults to now)

        Returns:
            Created JournalEntry with entry_id and sequence.

        Raises:
            IOError: If write fails (fail-closed).
        """
        self._sequence += 1
        entry = JournalEntry(
            entry_type=entry_type,
            data=data,
            timestamp=timestamp or datetime.now(timezone.utc).isoformat(),
            sequence=self._sequence,
        )

        line = entry.to_json() + "\n"
        line_bytes = line.encode("utf-8")

        with self._locked_append() as fd:
            written = os.write(fd, line_bytes)
            if written != len(line_bytes):
                raise IOError(f"Partial write: {written}/{len(line_bytes)} bytes")

        return entry

    def append_entry(self, entry: JournalEntry) -> JournalEntry:
        """
        Append existing JournalEntry.

        Updates sequence number.
        """
        return self.append(
            entry_type=entry.entry_type,
            data=entry.data,
            timestamp=entry.timestamp,
        )

    def read_all(self) -> List[JournalEntry]:
        """
        Read all entries from journal.

        Returns:
            List of JournalEntry objects.

        Raises:
            ValueError: If corruption detected.
        """
        if not self.path.exists():
            return []

        entries = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = JournalEntry.from_json(line)
                    entries.append(entry)
                except json.JSONDecodeError as e:
                    raise ValueError(
                        f"Journal corruption at line {line_no}: {e}"
                    )

        return entries

    def iter_entries(self) -> Iterator[JournalEntry]:
        """
        Iterate over entries (memory-efficient for large journals).

        Yields:
            JournalEntry objects.
        """
        if not self.path.exists():
            return

        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                yield JournalEntry.from_json(line)

    def find_by_type(self, entry_type: str) -> List[JournalEntry]:
        """Find all entries of given type."""
        return [e for e in self.iter_entries() if e.entry_type == entry_type]

    def find_by_filter(
        self,
        predicate: Callable[[JournalEntry], bool],
    ) -> List[JournalEntry]:
        """Find entries matching predicate."""
        return [e for e in self.iter_entries() if predicate(e)]

    def last_entry(self) -> Optional[JournalEntry]:
        """Get last entry (efficient for append-only journal)."""
        if not self.path.exists():
            return None

        last = None
        for entry in self.iter_entries():
            last = entry
        return last

    def entry_count(self) -> int:
        """Get total entry count."""
        return self._sequence

    def verify_integrity(self) -> bool:
        """
        Verify journal integrity.

        Returns:
            True if all entries are valid JSON.
        """
        try:
            list(self.iter_entries())
            return True
        except (json.JSONDecodeError, ValueError):
            return False
