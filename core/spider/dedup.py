# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T14:00:00Z
# Modified by: Claude (opus-4)
# Modified at (UTC): 2026-01-25T20:00:00Z
# Purpose: News deduplication store (sha256 JSONL format, atomic I/O, rotation-based cleanup)
# === END SIGNATURE ===
"""
News Deduplication Module

Tracks seen item IDs to avoid processing duplicates.
Uses sha256 JSONL format: sha256:<hex16> <json>

Backward compatible: reads legacy plain JSON lines during migration.
"""

import json
import os
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Set, Optional, Dict, Any

from core.io.atomic import (
    atomic_append_sha256_jsonl,
    parse_sha256_jsonl_line,
    format_sha256_jsonl_line,
    atomic_write_text,
)


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
                        # Try sha256 JSONL format first
                        if line.startswith("sha256:"):
                            entry = parse_sha256_jsonl_line(line)
                        else:
                            # Legacy plain JSON (backward compatible)
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
                    except (json.JSONDecodeError, ValueError):
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

        Uses sha256 JSONL format: sha256:<hex16> <json>

        Args:
            item_id: Item identifier
            source_id: Source that provided item
            link: Item URL for debugging

        Returns:
            True if added (was new), False if already present

        Raises:
            OSError: On I/O failure (fail-closed)
        """
        with self._lock:
            self._ensure_loaded()

            if item_id in self._cache:
                return False

            # Add to cache
            self._cache.add(item_id)

            # Persist to file with sha256 JSONL format
            entry = {
                "item_id": item_id,
                "source_id": source_id,
                "first_seen_utc": datetime.now(timezone.utc).isoformat(),
                "link": link,
            }

            # Atomic append with sha256 prefix
            atomic_append_sha256_jsonl(self._path, entry)

            return True

    def count(self) -> int:
        """Return number of entries in cache."""
        with self._lock:
            self._ensure_loaded()
            return len(self._cache)

    def rotate_if_needed(
        self,
        max_entries: int = 10000,
        max_bytes: int = 5 * 1024 * 1024,
    ) -> tuple[bool, str]:
        """
        Rotate dedup file if size limits exceeded.

        NEVER deletes original file. Creates backup and starts fresh.
        Policy: backup + new file + LATEST pointer (all atomic).

        Args:
            max_entries: Max entries before rotation (default: 10000)
            max_bytes: Max file size before rotation (default: 5MB)

        Returns:
            (rotated, backup_path_or_reason)
        """
        with self._lock:
            if not self._path.exists():
                return False, "no_file"

            # Check size limits
            try:
                file_size = self._path.stat().st_size
            except OSError:
                return False, "stat_error"

            entry_count = len(self._cache) if self._loaded else self._count_lines()

            needs_rotation = (entry_count > max_entries) or (file_size > max_bytes)

            if not needs_rotation:
                return False, "within_limits"

            # Generate backup filename with run_id
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_name = f"news_dedup.{ts}.bak.jsonl"
            backup_path = self._path.parent / backup_name

            # Step 1: Rename current to backup (atomic on same filesystem)
            try:
                os.rename(self._path, backup_path)
            except OSError as e:
                return False, f"rename_error: {e}"

            # Step 2: Create fresh empty file
            try:
                self._path.touch()
            except OSError as e:
                # Restore backup
                try:
                    os.rename(backup_path, self._path)
                except OSError:
                    pass
                return False, f"create_error: {e}"

            # Step 3: Write LATEST pointer
            latest_path = self._path.parent / "news_dedup.LATEST"
            try:
                atomic_write_text(latest_path, backup_name + "\n")
            except OSError:
                pass  # Non-fatal, backup still valid

            # Clear cache for fresh start
            self._cache.clear()
            self._loaded = True

            return True, str(backup_path)

    def _count_lines(self) -> int:
        """Count lines in dedup file without loading all."""
        if not self._path.exists():
            return 0
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                return sum(1 for line in f if line.strip())
        except OSError:
            return 0

    def clear_expired(self) -> int:
        """
        Mark expired entries (DEPRECATED - use rotate_if_needed).

        This method is kept for backward compatibility but now only
        triggers rotation if limits exceeded. It does NOT rewrite
        the file in place (that would violate append-only policy).

        Returns:
            Number of entries that would be removed (informational only)
        """
        with self._lock:
            if not self._path.exists():
                return 0

            cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
            expired_count = 0

            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            if line.startswith("sha256:"):
                                entry = parse_sha256_jsonl_line(line)
                            else:
                                entry = json.loads(line)

                            first_seen = entry.get("first_seen_utc", "")
                            if first_seen:
                                dt = datetime.fromisoformat(
                                    first_seen.replace("Z", "+00:00")
                                )
                                if dt < cutoff:
                                    expired_count += 1
                        except Exception:
                            expired_count += 1
            except Exception:
                return 0

            # Trigger rotation if many expired (instead of rewrite)
            if expired_count > 1000:
                self.rotate_if_needed()

            return expired_count

    def _needs_migration(self) -> bool:
        """Check if file contains legacy plain JSON that needs migration."""
        if not self._path.exists():
            return False

        try:
            with open(self._path, "r", encoding="utf-8") as f:
                first_line = f.readline().strip()
                if first_line and not first_line.startswith("sha256:"):
                    return True
        except Exception:
            pass

        return False


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
