# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T18:00:00Z
# Purpose: Atomic I/O operations with sha256 JSONL contract
# === END SIGNATURE ===
"""
Atomic I/O Module

All file writes use temp -> fsync -> replace pattern.
JSONL format: sha256:<hex> <json>\n

Fail-closed: Any I/O error raises, never silent corruption.
"""

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Union

# File locking for Windows
if sys.platform == "win32":
    import msvcrt

    def _lock_file(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_file(f):
        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except Exception:
            pass
else:
    import fcntl

    def _lock_file(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_file(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def compute_sha256(data: bytes) -> str:
    """Compute SHA256 hex digest of bytes."""
    return hashlib.sha256(data).hexdigest()


def compute_sha256_str(text: str) -> str:
    """Compute SHA256 hex digest of UTF-8 string."""
    return compute_sha256(text.encode("utf-8"))


def atomic_write_text(path: Union[str, Path], content: str, encoding: str = "utf-8") -> str:
    """
    Atomic text file write: temp -> fsync -> replace.

    Args:
        path: Target file path
        content: Text content to write
        encoding: Text encoding (default: utf-8)

    Returns:
        SHA256 hash of written content

    Raises:
        OSError: On any I/O failure (fail-closed)
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    content_bytes = content.encode(encoding)
    content_hash = compute_sha256(content_bytes)

    tmp_path = path.with_suffix(path.suffix + ".tmp")

    try:
        with open(tmp_path, "wb") as f:
            f.write(content_bytes)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_path, path)
        return content_hash

    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        raise


def atomic_write_json(path: Union[str, Path], data: Any, indent: int = 2) -> str:
    """
    Atomic JSON file write.

    Returns:
        SHA256 hash of JSON content
    """
    content = json.dumps(data, ensure_ascii=False, indent=indent)
    return atomic_write_text(path, content)


def format_sha256_jsonl_line(obj: Dict[str, Any]) -> str:
    """
    Format object as sha256 JSONL line.

    Format: sha256:<hex16> <json>\n

    Args:
        obj: JSON-serializable dict

    Returns:
        Formatted line with sha256 prefix
    """
    json_str = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    json_bytes = json_str.encode("utf-8")
    sha = compute_sha256(json_bytes)[:16]  # First 16 chars
    return f"sha256:{sha} {json_str}\n"


def parse_sha256_jsonl_line(line: str) -> Dict[str, Any]:
    """
    Parse and validate sha256 JSONL line.

    Format: sha256:<hex16> <json>

    Args:
        line: Line to parse (with or without trailing newline)

    Returns:
        Parsed JSON object

    Raises:
        ValueError: If format invalid or hash mismatch (fail-closed)
    """
    line = line.strip()
    if not line:
        raise ValueError("Empty line")

    if not line.startswith("sha256:"):
        raise ValueError(f"Missing sha256 prefix: {line[:50]}...")

    # Split: sha256:<hex16> <json>
    try:
        prefix_end = line.index(" ", 7)  # After "sha256:"
        expected_sha = line[7:prefix_end]
        json_str = line[prefix_end + 1:]
    except ValueError:
        raise ValueError(f"Invalid sha256 JSONL format: {line[:50]}...")

    # Validate hash
    json_bytes = json_str.encode("utf-8")
    actual_sha = compute_sha256(json_bytes)[:16]

    if actual_sha != expected_sha:
        raise ValueError(
            f"SHA256 mismatch: expected={expected_sha}, actual={actual_sha}"
        )

    return json.loads(json_str)


def atomic_append_sha256_jsonl(
    path: Union[str, Path],
    obj: Dict[str, Any],
    max_retries: int = 3,
) -> bool:
    """
    Atomically append sha256 JSONL line to file.

    Uses file locking to prevent race conditions.

    Args:
        path: JSONL file path
        obj: Object to append
        max_retries: Lock acquisition retries

    Returns:
        True if successful

    Raises:
        OSError: On persistent I/O failure (fail-closed)
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    line = format_sha256_jsonl_line(obj)

    for attempt in range(max_retries):
        try:
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                try:
                    _lock_file(f)
                    f.write(line)
                    f.flush()
                    os.fsync(f.fileno())
                    return True
                finally:
                    _unlock_file(f)

        except (IOError, OSError) as e:
            if attempt == max_retries - 1:
                raise OSError(f"Failed to append to {path} after {max_retries} attempts: {e}")
            import time
            time.sleep(0.1 * (attempt + 1))

    return False


def read_sha256_jsonl(path: Union[str, Path], skip_invalid: bool = False) -> list:
    """
    Read and validate all lines from sha256 JSONL file.

    Args:
        path: JSONL file path
        skip_invalid: If True, skip invalid lines (log warning). If False, raise on first invalid.

    Returns:
        List of parsed objects

    Raises:
        ValueError: If skip_invalid=False and any line is invalid
    """
    import logging
    logger = logging.getLogger("atomic_io")

    path = Path(path)
    if not path.exists():
        return []

    results = []
    with open(path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue

            try:
                obj = parse_sha256_jsonl_line(line)
                results.append(obj)
            except ValueError as e:
                if skip_invalid:
                    logger.warning("Line %d invalid in %s: %s", line_num, path, e)
                else:
                    raise ValueError(f"Line {line_num} in {path}: {e}")

    return results


class AtomicFileLock:
    """
    File-based lock for preventing concurrent access.

    Usage:
        lock = AtomicFileLock(Path("state/spider.lock"))
        if lock.acquire(timeout_sec=10):
            try:
                # do work
            finally:
                lock.release()
    """

    def __init__(self, lock_path: Union[str, Path]):
        self.lock_path = Path(lock_path)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = None

    def acquire(self, timeout_sec: float = 30.0) -> bool:
        """
        Acquire lock with timeout.

        Returns:
            True if acquired, False if timeout
        """
        import time

        start = time.time()
        while time.time() - start < timeout_sec:
            try:
                # O_EXCL = fail if exists
                self._fd = os.open(
                    str(self.lock_path),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY
                )
                # Write PID for debugging
                os.write(self._fd, f"{os.getpid()}\n".encode())
                os.fsync(self._fd)
                return True

            except FileExistsError:
                # Check if stale (holder dead)
                if self._is_stale():
                    try:
                        self.lock_path.unlink()
                        continue
                    except Exception:
                        pass
                time.sleep(0.5)

            except Exception:
                time.sleep(0.5)

        return False

    def _is_stale(self) -> bool:
        """Check if lock holder is dead."""
        try:
            pid_str = self.lock_path.read_text().strip()
            pid = int(pid_str)
            # Check if process exists
            if sys.platform == "win32":
                import ctypes
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                h = ctypes.windll.kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION, 0, pid
                )
                if h:
                    ctypes.windll.kernel32.CloseHandle(h)
                    return False  # Process alive
                return True  # Process dead
            else:
                os.kill(pid, 0)
                return False
        except Exception:
            return True  # Assume stale on any error

    def release(self):
        """Release lock."""
        if self._fd is not None:
            try:
                os.close(self._fd)
            except Exception:
                pass
            self._fd = None

        try:
            self.lock_path.unlink()
        except Exception:
            pass

    def __enter__(self):
        if not self.acquire():
            raise TimeoutError(f"Could not acquire lock: {self.lock_path}")
        return self

    def __exit__(self, *args):
        self.release()
