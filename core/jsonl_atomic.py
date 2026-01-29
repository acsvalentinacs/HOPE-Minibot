# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 02:47:00 UTC
# Purpose: Atomic JSONL operations with lock + fsync + sha256 verification
# Contract: Every append is atomic, locked, and sha256-verified
# === END SIGNATURE ===
"""
ATOMIC JSONL OPERATIONS

Обеспечивает:
1. File locking (межпроцессная защита)
2. Atomic append (lock → append → flush → fsync)
3. SHA256 verification после записи
4. Corrupt detection и recovery

Использование:
    from core.jsonl_atomic import append_jsonl, read_jsonl, verify_jsonl

    # Атомарный append с автоматическим sha256
    success = append_jsonl(path, {"event": "SIGNAL", "data": {...}})

    # Чтение с валидацией
    entries, errors = read_jsonl(path, validate=True)

    # Проверка целостности
    result = verify_jsonl(path)
"""

import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple
import logging

# Platform-specific imports
if sys.platform != "win32":
    import fcntl
else:
    fcntl = None  # Will use msvcrt on Windows

from core.sha256_contract import add_sha256, verify_sha256, is_valid_sha256_format

log = logging.getLogger("JSONL-ATOMIC")


class JsonlLockError(Exception):
    """Failed to acquire file lock."""
    pass


class JsonlWriteError(Exception):
    """Failed to write to JSONL file."""
    pass


class JsonlIntegrityError(Exception):
    """JSONL file integrity check failed."""
    pass


@contextmanager
def file_lock(path: Path, timeout: float = 5.0) -> Generator[None, None, None]:
    """
    Cross-platform file lock context manager.

    Args:
        path: Path to file (lock will be on .lock file)
        timeout: Max seconds to wait for lock

    Raises:
        JsonlLockError: If lock cannot be acquired
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    start = time.time()
    lock_fd = None

    try:
        while True:
            try:
                # Open lock file
                lock_fd = os.open(
                    str(lock_path),
                    os.O_RDWR | os.O_CREAT,
                    0o644
                )

                # Try to acquire exclusive lock
                if sys.platform == "win32":
                    # Windows: use msvcrt
                    import msvcrt
                    try:
                        msvcrt.locking(lock_fd, msvcrt.LK_NBLCK, 1)
                        break
                    except IOError:
                        os.close(lock_fd)
                        lock_fd = None
                        if time.time() - start > timeout:
                            raise JsonlLockError(f"Timeout acquiring lock: {lock_path}")
                        time.sleep(0.1)
                else:
                    # Unix: use fcntl
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break

            except (IOError, OSError) as e:
                if lock_fd:
                    os.close(lock_fd)
                    lock_fd = None

                if time.time() - start > timeout:
                    raise JsonlLockError(f"Timeout acquiring lock: {lock_path}") from e

                time.sleep(0.1)

        yield

    finally:
        if lock_fd is not None:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    try:
                        msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
                    except Exception:
                        pass
                else:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except Exception:
                pass
            finally:
                os.close(lock_fd)


def append_jsonl(
    path: Path,
    entry: Dict[str, Any],
    add_sha: bool = True,
    verify_after: bool = True
) -> bool:
    """
    Atomically append entry to JSONL file.

    Process:
    1. Acquire file lock
    2. Add sha256 if requested
    3. Append JSON line
    4. Flush + fsync
    5. Verify sha256 if requested
    6. Release lock

    Args:
        path: Path to JSONL file
        entry: Dictionary to append
        add_sha: Add sha256 field if not present
        verify_after: Verify sha256 after write

    Returns:
        True if successful

    Raises:
        JsonlWriteError: If write fails
        JsonlIntegrityError: If verification fails
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Add sha256 if needed
    if add_sha and "sha256" not in entry:
        entry = add_sha256(entry)

    # Serialize
    line = json.dumps(entry, ensure_ascii=False) + "\n"

    with file_lock(path):
        try:
            # Append with fsync
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())

            # Verify if requested
            if verify_after and "sha256" in entry:
                # Read back last line
                with open(path, "rb") as f:
                    f.seek(0, 2)  # End
                    size = f.tell()
                    # Read last ~4KB to find last line
                    read_size = min(4096, size)
                    f.seek(size - read_size)
                    tail = f.read().decode("utf-8")
                    lines = tail.strip().split("\n")
                    if lines:
                        last = json.loads(lines[-1])
                        is_valid, expected = verify_sha256(last)
                        if not is_valid:
                            raise JsonlIntegrityError(
                                f"SHA256 mismatch after write: got {last.get('sha256')}, expected {expected}"
                            )

            return True

        except JsonlIntegrityError:
            raise
        except Exception as e:
            raise JsonlWriteError(f"Failed to append to {path}: {e}") from e


def read_jsonl(
    path: Path,
    validate: bool = False,
    skip_invalid: bool = True
) -> Tuple[List[Dict], List[Dict]]:
    """
    Read JSONL file with optional validation.

    Args:
        path: Path to JSONL file
        validate: Verify sha256 for each entry
        skip_invalid: If True, skip invalid entries; if False, raise error

    Returns:
        Tuple of (valid_entries, errors)
        errors format: [{"line": N, "error": "...", "raw": "..."}]
    """
    path = Path(path)
    entries = []
    errors = []

    if not path.exists():
        return [], []

    with file_lock(path):
        with open(path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue

                try:
                    entry = json.loads(line)

                    if validate and "sha256" in entry:
                        is_valid, expected = verify_sha256(entry)
                        if not is_valid:
                            error = {
                                "line": line_num,
                                "error": f"SHA256 mismatch: got {entry.get('sha256')}, expected {expected}",
                                "raw": line[:200]
                            }
                            errors.append(error)
                            if not skip_invalid:
                                raise JsonlIntegrityError(error["error"])
                            continue

                    entries.append(entry)

                except json.JSONDecodeError as e:
                    error = {
                        "line": line_num,
                        "error": f"JSON decode error: {e}",
                        "raw": line[:200]
                    }
                    errors.append(error)
                    if not skip_invalid:
                        raise JsonlIntegrityError(error["error"])

    return entries, errors


def verify_jsonl(path: Path) -> Dict[str, Any]:
    """
    Verify JSONL file integrity.

    Returns:
        {
            "valid": bool,
            "total": int,
            "valid_count": int,
            "no_sha": int,
            "bad_sha": int,
            "bad_json": int,
            "errors": [...]
        }
    """
    path = Path(path)
    result = {
        "valid": True,
        "total": 0,
        "valid_count": 0,
        "no_sha": 0,
        "bad_sha": 0,
        "bad_json": 0,
        "errors": []
    }

    if not path.exists():
        result["valid"] = False
        result["errors"].append({"error": "File not found"})
        return result

    with file_lock(path):
        with open(path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue

                result["total"] += 1

                try:
                    entry = json.loads(line)
                except json.JSONDecodeError as e:
                    result["bad_json"] += 1
                    result["valid"] = False
                    result["errors"].append({
                        "line": line_num,
                        "error": f"JSON decode: {e}"
                    })
                    continue

                sha = entry.get("sha256")
                if not sha:
                    result["no_sha"] += 1
                    result["valid"] = False
                    result["errors"].append({
                        "line": line_num,
                        "error": "Missing sha256"
                    })
                    continue

                is_valid, expected = verify_sha256(entry)
                if not is_valid:
                    result["bad_sha"] += 1
                    result["valid"] = False
                    result["errors"].append({
                        "line": line_num,
                        "error": f"SHA256 mismatch: {sha} != {expected}"
                    })
                    continue

                result["valid_count"] += 1

    return result


def atomic_write_jsonl(path: Path, entries: List[Dict], add_sha: bool = True) -> bool:
    """
    Atomically write entire JSONL file (temp → fsync → replace).

    Args:
        path: Target path
        entries: List of entries to write
        add_sha: Add sha256 to entries without it

    Returns:
        True if successful
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")

    with file_lock(path):
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                for entry in entries:
                    if add_sha and "sha256" not in entry:
                        entry = add_sha256(entry)
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())

            os.replace(str(tmp), str(path))
            return True

        except Exception as e:
            if tmp.exists():
                try:
                    tmp.unlink()
                except Exception:
                    pass
            raise JsonlWriteError(f"Failed to write {path}: {e}") from e
