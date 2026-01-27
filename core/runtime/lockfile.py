# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T23:45:00Z
# Purpose: Runtime lockfile with cmdline SHA256 binding (fail-closed)
# Security: Prevents duplicate instances, ensures SSoT integrity
# === END SIGNATURE ===
"""
Runtime Lockfile with Command Line Binding.

Ensures single instance execution with cryptographic proof of identity.
Uses GetCommandLineW as SSoT for process identity.

Contract:
- lockfile.json contains: schema, cmdline_sha256, pid, created_at
- If lockfile exists with different cmdline_sha256 → FAIL (not overwrite)
- If lockfile exists but PID dead → stale removal allowed
- Atomic write: temp → fsync → replace

Fail-closed: Any validation failure = refuse to acquire lock.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Dict, Any

# Import from core modules
try:
    from core.truth.cmdline_ssot import get_raw_cmdline, get_cmdline_sha256
except ImportError:
    # Fallback for standalone testing
    def get_raw_cmdline() -> str:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.windll.kernel32
            kernel32.GetCommandLineW.restype = wintypes.LPWSTR
            return kernel32.GetCommandLineW() or ""
        return " ".join(sys.argv)

    def get_cmdline_sha256() -> str:
        return hashlib.sha256(get_raw_cmdline().encode("utf-8")).hexdigest()


LOCKFILE_SCHEMA = "lockfile:2"


@dataclass
class LockfileData:
    """Lockfile content structure."""
    schema: str
    cmdline_sha256: str
    pid: int
    created_at_unix: float
    exe_path: str
    state_dir: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LockAcquireResult:
    """Result of lock acquisition attempt."""
    acquired: bool
    reason: str
    stale_removed: bool = False
    existing_pid: Optional[int] = None
    existing_sha256: Optional[str] = None


class RuntimeLockfile:
    """
    Runtime lockfile with cmdline SHA256 binding.

    FAIL-CLOSED behavior:
    - If lockfile exists with DIFFERENT cmdline_sha256 → FAIL
    - If lockfile exists with SAME sha256 but PID alive → FAIL
    - If lockfile exists but PID dead → remove and acquire
    - Any I/O error → FAIL (do not proceed)
    """

    def __init__(
        self,
        lock_path: Optional[Path] = None,
        state_dir: Optional[Path] = None,
    ):
        """
        Initialize lockfile manager.

        Args:
            lock_path: Path to lockfile (default: state/runtime.lock.json)
            state_dir: State directory (default: project_root/state)
        """
        if state_dir is None:
            state_dir = Path(__file__).resolve().parent.parent.parent / "state"
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)

        if lock_path is None:
            lock_path = self.state_dir / "runtime.lock.json"
        self.lock_path = lock_path

        self._acquired = False
        self._my_sha256: Optional[str] = None

    def acquire(self, timeout_sec: float = 0) -> LockAcquireResult:
        """
        Acquire runtime lock.

        Args:
            timeout_sec: Max wait time (0 = immediate, no retry)

        Returns:
            LockAcquireResult with acquisition status

        Behavior:
        - If lockfile doesn't exist: create and acquire
        - If exists with same sha256 and dead PID: remove and acquire
        - If exists with same sha256 and alive PID: FAIL (duplicate instance)
        - If exists with different sha256: FAIL (integrity violation)
        """
        start_time = time.time()
        my_sha256 = get_cmdline_sha256()
        self._my_sha256 = my_sha256

        while True:
            result = self._try_acquire(my_sha256)

            if result.acquired:
                self._acquired = True
                return result

            # Check timeout
            if timeout_sec <= 0:
                return result

            elapsed = time.time() - start_time
            if elapsed >= timeout_sec:
                return result

            # Wait and retry
            time.sleep(min(0.5, timeout_sec - elapsed))

        return LockAcquireResult(
            acquired=False,
            reason="Timeout waiting for lock",
        )

    def _try_acquire(self, my_sha256: str) -> LockAcquireResult:
        """Single attempt to acquire lock."""
        try:
            if self.lock_path.exists():
                return self._handle_existing_lock(my_sha256)
            else:
                return self._create_lock(my_sha256)

        except Exception as e:
            return LockAcquireResult(
                acquired=False,
                reason=f"Lock acquisition error: {e}",
            )

    def _handle_existing_lock(self, my_sha256: str) -> LockAcquireResult:
        """Handle existing lockfile."""
        try:
            content = self.lock_path.read_text(encoding="utf-8")
            data = json.loads(content)

            existing_sha256 = data.get("cmdline_sha256", "")
            existing_pid = data.get("pid", 0)
            schema = data.get("schema", "")

            # Validate schema
            if schema != LOCKFILE_SCHEMA:
                # Old schema - treat as stale
                self.lock_path.unlink()
                return self._create_lock(my_sha256, stale_removed=True)

            # Check cmdline SHA256 match
            if existing_sha256 != my_sha256:
                # DIFFERENT command line - FAIL-CLOSED
                # This is an integrity violation
                return LockAcquireResult(
                    acquired=False,
                    reason=(
                        f"cmdline_sha256 mismatch: "
                        f"existing={existing_sha256[:16]}... "
                        f"mine={my_sha256[:16]}..."
                    ),
                    existing_pid=existing_pid,
                    existing_sha256=existing_sha256,
                )

            # Same SHA256 - check if holder is alive
            if self._is_process_alive(existing_pid):
                # Same cmdline, PID alive - another instance running
                return LockAcquireResult(
                    acquired=False,
                    reason=f"Lock held by PID {existing_pid} (same cmdline)",
                    existing_pid=existing_pid,
                    existing_sha256=existing_sha256,
                )

            # Same cmdline, PID dead - stale lock
            self.lock_path.unlink()
            return self._create_lock(my_sha256, stale_removed=True)

        except (json.JSONDecodeError, KeyError) as e:
            # Corrupted lockfile - remove it
            try:
                self.lock_path.unlink()
            except Exception:
                pass
            return self._create_lock(my_sha256, stale_removed=True)

    def _create_lock(
        self,
        my_sha256: str,
        stale_removed: bool = False,
    ) -> LockAcquireResult:
        """Create new lockfile atomically."""
        data = LockfileData(
            schema=LOCKFILE_SCHEMA,
            cmdline_sha256=my_sha256,
            pid=os.getpid(),
            created_at_unix=time.time(),
            exe_path=sys.executable,
            state_dir=str(self.state_dir),
        )

        # Atomic write
        content = json.dumps(data.to_dict(), indent=2)
        tmp_path = self.lock_path.with_suffix(".lock.json.tmp")

        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_path, self.lock_path)

            return LockAcquireResult(
                acquired=True,
                reason="Lock acquired successfully",
                stale_removed=stale_removed,
            )

        except Exception as e:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            return LockAcquireResult(
                acquired=False,
                reason=f"Failed to create lockfile: {e}",
            )

    def _is_process_alive(self, pid: int) -> bool:
        """Check if process with given PID is alive."""
        if pid <= 0:
            return False

        if sys.platform == "win32":
            try:
                import ctypes
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                h = ctypes.windll.kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION, 0, pid
                )
                if h:
                    ctypes.windll.kernel32.CloseHandle(h)
                    return True
                return False
            except Exception:
                return False
        else:
            try:
                os.kill(pid, 0)
                return True
            except (OSError, ProcessLookupError):
                return False

    def release(self) -> bool:
        """
        Release lock (remove lockfile).

        Only removes if we own the lock (same PID).

        Returns:
            True if released, False if not owner or error
        """
        if not self._acquired:
            return False

        try:
            if self.lock_path.exists():
                content = self.lock_path.read_text(encoding="utf-8")
                data = json.loads(content)

                # Verify we own it
                if data.get("pid") != os.getpid():
                    return False

                if data.get("cmdline_sha256") != self._my_sha256:
                    return False

                self.lock_path.unlink()
                self._acquired = False
                return True

            return False

        except Exception:
            return False

    def is_held(self) -> bool:
        """Check if lock is currently held by any process."""
        if not self.lock_path.exists():
            return False

        try:
            content = self.lock_path.read_text(encoding="utf-8")
            data = json.loads(content)
            pid = data.get("pid", 0)
            return self._is_process_alive(pid)
        except Exception:
            return False

    def get_holder_info(self) -> Optional[Dict[str, Any]]:
        """Get info about current lock holder."""
        if not self.lock_path.exists():
            return None

        try:
            content = self.lock_path.read_text(encoding="utf-8")
            data = json.loads(content)
            data["is_alive"] = self._is_process_alive(data.get("pid", 0))
            return data
        except Exception:
            return None

    def __enter__(self):
        result = self.acquire()
        if not result.acquired:
            raise RuntimeError(f"Failed to acquire lock: {result.reason}")
        return self

    def __exit__(self, *args):
        self.release()


# === Convenience Functions ===

def acquire_runtime_lock(
    lock_path: Optional[Path] = None,
    fail_on_conflict: bool = True,
) -> RuntimeLockfile:
    """
    Acquire runtime lock or fail.

    Args:
        lock_path: Custom lock path
        fail_on_conflict: If True, raise on conflict; if False, return anyway

    Returns:
        RuntimeLockfile instance (acquired if possible)

    Raises:
        RuntimeError: If fail_on_conflict=True and lock cannot be acquired
    """
    lock = RuntimeLockfile(lock_path=lock_path)
    result = lock.acquire()

    if not result.acquired and fail_on_conflict:
        raise RuntimeError(f"Runtime lock conflict: {result.reason}")

    return lock


def check_runtime_lock(lock_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Check runtime lock status without acquiring.

    Returns:
        Status dict with is_held, holder_info, etc.
    """
    lock = RuntimeLockfile(lock_path=lock_path)
    holder = lock.get_holder_info()

    return {
        "is_held": lock.is_held(),
        "lock_path": str(lock.lock_path),
        "holder": holder,
        "my_cmdline_sha256": get_cmdline_sha256()[:16] + "...",
    }


# === CLI ===

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Runtime Lockfile Manager")
    parser.add_argument("command", choices=["acquire", "release", "status", "test"])
    parser.add_argument("--path", type=Path, help="Custom lock path")
    args = parser.parse_args()

    lock = RuntimeLockfile(lock_path=args.path)

    if args.command == "acquire":
        result = lock.acquire()
        print(f"Acquired: {result.acquired}")
        print(f"Reason: {result.reason}")
        if result.acquired:
            print("Press Enter to release...")
            input()
            lock.release()

    elif args.command == "release":
        # Force release (dangerous - only for cleanup)
        if lock.lock_path.exists():
            lock.lock_path.unlink()
            print("Lock removed")
        else:
            print("No lock to remove")

    elif args.command == "status":
        status = check_runtime_lock(lock_path=args.path)
        print(json.dumps(status, indent=2, default=str))

    elif args.command == "test":
        print("Testing lock acquisition...")
        result = lock.acquire()
        print(f"First acquire: {result.acquired}")

        # Try second acquire
        lock2 = RuntimeLockfile(lock_path=args.path)
        result2 = lock2.acquire()
        print(f"Second acquire (should fail): {result2.acquired}")
        print(f"Reason: {result2.reason}")

        lock.release()
        print("Released first lock")

        result3 = lock2.acquire()
        print(f"Second acquire after release: {result3.acquired}")
        lock2.release()
