# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T14:00:00Z
# Purpose: News deduplication store (stdlib-only, JSONL append)
# === END SIGNATURE ===
"""
News Deduplication Module

Tracks seen item IDs to avoid processing duplicates.
Uses atomic JSONL append for persistence.
"""

import json
import os
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Set, Optional, Dict, Any

# Windows-specific locking
if sys.platform == "win32":
    import msvcrt

    def _lock_file(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock_file(f):
        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except Exception:
            pass
else:
    import fcntl

    def _lock_file(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)

    def _unlock_file(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


@dataclass
class DedupEntry:
    """Entry in dedup store."""
    item_id: str
    source_id: str
    first_seen_utc: str
    link: str = ""


class DedupStore:
    """
    Persistent deduplication store using JSONL file.

    Thread-safe with file locking for concurrent access.
    Entries are kept for configurable retention period.
    """

    def __init__(
        self,
        store_path: Optional[Path] = None,
        retention_days: int = 7,
        project_root: Optional[Path] = None,
    ):
        """
        Initialize dedup store.

        Args:
            store_path: Path to JSONL file (default: state/news_dedup.jsonl)
            retention_days: Days to keep entries before expiry
            project_root: Project root for resolving paths
        """
        if project_root is None:
            project_root = Path(__file__).resolve().parent.parent.parent

        if store_path is None:
            store_path = project_root / "state" / "news_dedup.jsonl"

        self._path = store_path
        self._retention_days = retention_days
        self._lock = threading.Lock()
        self._cache: Set[str] = set()
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Load existing entries into memory cache."""
        if self._loaded:
            return

        self._path.parent.mkdir(parents=True, exist_ok=True)

        if not self._path.exists():
            self._loaded = True
            return

        cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days)

        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        # Check expiry
                        first_seen = entry.get("first_seen_utc", "")
                        if first_seen:
                            try:
                                dt = datetime.fromisoformat(
                                    first_seen.replace("Z", "+00:00")
                                )
                                if dt < cutoff:
                                    continue  # Expired
                            except Exception:
                                pass
                        self._cache.add(entry.get("item_id", ""))
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass

        self._loaded = True

    def contains(self, item_id: str) -> bool:
        """
        Check if item ID is in dedup store.

        Args:
            item_id: Item identifier to check

        Returns:
            True if item was seen before
        """
        with self._lock:
            self._ensure_loaded()
            return item_id in self._cache

    def add(
        self,
        item_id: str,
        source_id: str,
        link: str = "",
    ) -> bool:
        """
        Add item to dedup store if not present.

        Args:
            item_id: Item identifier
            source_id: Source that provided item
            link: Item URL for debugging

        Returns:
            True if added (was new), False if already present
        """
        with self._lock:
            self._ensure_loaded()

            if item_id in self._cache:
                return False

            # Add to cache
            self._cache.add(item_id)

            # Persist to file with locking
            entry = {
                "item_id": item_id,
                "source_id": source_id,
                "first_seen_utc": datetime.now(timezone.utc).isoformat(),
                "link": link,
            }

            self._path.parent.mkdir(parents=True, exist_ok=True)

            # Atomic append with lock
            with open(self._path, "a", encoding="utf-8") as f:
                _lock_file(f)
                try:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                finally:
                    _unlock_file(f)

            return True

    def count(self) -> int:
        """Return number of entries in cache."""
        with self._lock:
            self._ensure_loaded()
            return len(self._cache)

    def clear_expired(self) -> int:
        """
        Remove expired entries from store file.

        Rewrites file with only non-expired entries.

        Returns:
            Number of entries removed
        """
        with self._lock:
            if not self._path.exists():
                return 0

            cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
            kept = []
            removed = 0

            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    _lock_file(f)
                    try:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                entry = json.loads(line)
                                first_seen = entry.get("first_seen_utc", "")
                                if first_seen:
                                    dt = datetime.fromisoformat(
                                        first_seen.replace("Z", "+00:00")
                                    )
                                    if dt < cutoff:
                                        removed += 1
                                        continue
                                kept.append(line)
                            except Exception:
                                kept.append(line)  # Keep malformed
                    finally:
                        _unlock_file(f)
            except Exception:
                return 0

            if removed > 0:
                # Rewrite file
                tmp = self._path.with_suffix(".tmp")
                try:
                    with open(tmp, "w", encoding="utf-8") as f:
                        for line in kept:
                            f.write(line + "\n")
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, self._path)

                    # Update cache
                    self._cache.clear()
                    for line in kept:
                        try:
                            entry = json.loads(line)
                            self._cache.add(entry.get("item_id", ""))
                        except Exception:
                            pass
                except Exception:
                    if tmp.exists():
                        tmp.unlink()
                    raise

            return removed


def is_duplicate(
    item_id: str,
    store: Optional[DedupStore] = None,
) -> bool:
    """
    Convenience function to check if item is duplicate.

    Uses global store if not provided.
    """
    if store is None:
        store = _get_global_store()
    return store.contains(item_id)


# Global store singleton
_global_store: Optional[DedupStore] = None
_global_lock = threading.Lock()


def _get_global_store() -> DedupStore:
    """Get or create global dedup store."""
    global _global_store
    with _global_lock:
        if _global_store is None:
            _global_store = DedupStore()
        return _global_store
