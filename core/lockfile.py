# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 02:20:00 UTC
# Purpose: Single-instance process locking with PID files
# Contract: Atomic lockfile with stale detection
# === END SIGNATURE ===
"""
SINGLE-INSTANCE LOCKFILE

Prevents multiple instances of Gateway/AutoTrader from running.
Uses atomic PID files with stale process detection.

Usage:
    from core.lockfile import ProcessLock

    lock = ProcessLock("gateway")
    if not lock.acquire():
        print(f"Another instance running (PID {lock.get_owner()})")
        sys.exit(1)

    try:
        # Run your service
        run_gateway()
    finally:
        lock.release()
"""

import os
import sys
import time
import atexit
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger("LOCKFILE")

LOCK_DIR = Path("state/locks")


def is_process_running(pid: int) -> bool:
    """Check if a process with given PID is running."""
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            # Fallback: try to send signal 0
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


class ProcessLock:
    """
    Single-instance process lock using PID files.

    Features:
    - Atomic lockfile creation
    - Stale lock detection (process no longer running)
    - Auto-cleanup on exit
    - Heartbeat support (optional)
    """

    def __init__(self, name: str, lock_dir: Path = None):
        self.name = name
        self.lock_dir = lock_dir or LOCK_DIR
        self.lock_file = self.lock_dir / f"{name}.lock"
        self.pid = os.getpid()
        self._acquired = False

    def acquire(self, timeout: float = 0) -> bool:
        """
        Try to acquire the lock.

        Args:
            timeout: Max seconds to wait (0 = no wait)

        Returns:
            True if lock acquired, False if another instance running
        """
        self.lock_dir.mkdir(parents=True, exist_ok=True)

        start = time.time()
        while True:
            # Check for existing lock
            owner_pid = self.get_owner()

            if owner_pid is None:
                # No lock exists, try to create
                if self._create_lock():
                    self._acquired = True
                    atexit.register(self.release)
                    log.info(f"Lock acquired: {self.name} (PID {self.pid})")
                    return True

            elif owner_pid == self.pid:
                # We already own the lock
                self._acquired = True
                return True

            elif not is_process_running(owner_pid):
                # Stale lock - owner process is dead
                log.warning(f"Removing stale lock: {self.name} (was PID {owner_pid})")
                self._remove_stale_lock()
                continue  # Retry

            else:
                # Another instance is running
                if timeout > 0 and (time.time() - start) < timeout:
                    time.sleep(0.1)
                    continue
                log.warning(f"Lock held by another process: {self.name} (PID {owner_pid})")
                return False

    def release(self) -> None:
        """Release the lock."""
        if not self._acquired:
            return

        try:
            if self.lock_file.exists():
                owner = self.get_owner()
                if owner == self.pid:
                    self.lock_file.unlink()
                    log.info(f"Lock released: {self.name}")
        except Exception as e:
            log.error(f"Failed to release lock: {e}")
        finally:
            self._acquired = False

    def get_owner(self) -> Optional[int]:
        """Get PID of current lock owner, or None if not locked."""
        try:
            if not self.lock_file.exists():
                return None

            content = self.lock_file.read_text(encoding="utf-8").strip()
            return int(content)
        except (ValueError, OSError):
            return None

    def _create_lock(self) -> bool:
        """Atomically create lock file."""
        tmp = self.lock_file.with_suffix(".tmp")

        try:
            # Write PID to temp file
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(str(self.pid))
                f.flush()
                os.fsync(f.fileno())

            # Atomic rename (fails if file exists on some systems)
            if sys.platform == "win32":
                # Windows: check and rename
                if self.lock_file.exists():
                    tmp.unlink()
                    return False
                os.rename(str(tmp), str(self.lock_file))
            else:
                # Unix: use link for atomicity
                try:
                    os.link(str(tmp), str(self.lock_file))
                    tmp.unlink()
                except FileExistsError:
                    tmp.unlink()
                    return False

            return True

        except Exception as e:
            log.error(f"Failed to create lock: {e}")
            if tmp.exists():
                try:
                    tmp.unlink()
                except Exception:
                    pass
            return False

    def _remove_stale_lock(self) -> None:
        """Remove a stale lock file."""
        try:
            if self.lock_file.exists():
                self.lock_file.unlink()
        except Exception as e:
            log.error(f"Failed to remove stale lock: {e}")

    def heartbeat(self) -> None:
        """Update lock timestamp (for monitoring)."""
        if self._acquired and self.lock_file.exists():
            try:
                self.lock_file.touch()
            except Exception:
                pass

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError(f"Failed to acquire lock: {self.name}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False


# Convenience functions
def acquire_lock(name: str) -> Optional[ProcessLock]:
    """Acquire a process lock, return None if failed."""
    lock = ProcessLock(name)
    if lock.acquire():
        return lock
    return None


def is_locked(name: str) -> bool:
    """Check if a service is locked (running)."""
    lock = ProcessLock(name)
    owner = lock.get_owner()
    if owner is None:
        return False
    return is_process_running(owner)


def get_lock_owner(name: str) -> Optional[int]:
    """Get PID of lock owner, or None."""
    return ProcessLock(name).get_owner()
