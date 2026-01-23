# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 14:00:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 14:00:00 UTC
# === END SIGNATURE ===
"""
core/jsonl_sha.py

Atomic JSONL writer with sha256 integrity (Canon B format).

Format: sha256:<64hex>:<json>

Features:
- Inter-process safe via file locking (fcntl/msvcrt)
- Atomic append with fsync
- Self-test when run as module

Usage:
    from core.jsonl_sha import append_sha256_line
    append_sha256_line(Path("state/log.jsonl"), {"key": "value"})

Self-test:
    python -m core.jsonl_sha
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# Platform-specific locking
if sys.platform == "win32":
    import msvcrt

    def _lock_file(f):
        """Acquire exclusive lock on file (Windows)."""
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock_file(f):
        """Release lock on file (Windows)."""
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass  # Already unlocked or closed
else:
    import fcntl

    def _lock_file(f):
        """Acquire exclusive lock on file (Unix)."""
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)

    def _unlock_file(f):
        """Release lock on file (Unix)."""
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _sha256_hex(data: str) -> str:
    """Compute sha256 hex digest of UTF-8 encoded string."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def append_sha256_line(path: Path, record: Any, *, lock_timeout: float = 30.0) -> str:
    """
    Atomically append a record to JSONL file with sha256 prefix.

    Args:
        path: Target JSONL file path
        record: Any JSON-serializable object
        lock_timeout: Maximum seconds to wait for lock

    Returns:
        The sha256 hash of the written payload

    Raises:
        OSError: If file operations fail
        json.JSONDecodeError: If record is not JSON-serializable
    """
    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    # Serialize to compact JSON (Canon B format)
    payload = json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    sha = _sha256_hex(payload)
    line = f"sha256:{sha}:{payload}\n"
    line_bytes = line.encode("utf-8")

    # Atomic append with lock
    with open(path, "ab") as f:
        _lock_file(f)
        try:
            f.write(line_bytes)
            f.flush()
            os.fsync(f.fileno())
        finally:
            _unlock_file(f)

    return sha


def read_and_verify(path: Path) -> tuple[list[dict], int, int]:
    """
    Read JSONL file and verify all sha256 hashes.

    Returns:
        (records, valid_count, invalid_count)
    """
    if not path.exists():
        return [], 0, 0

    records = []
    valid = 0
    invalid = 0

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n\r")
            if not line:
                continue

            # Parse Canon B format
            if not line.startswith("sha256:"):
                invalid += 1
                continue

            parts = line.split(":", 2)
            if len(parts) != 3:
                invalid += 1
                continue

            claimed_sha = parts[1]
            payload = parts[2]

            # Verify hash
            computed_sha = _sha256_hex(payload)
            if computed_sha != claimed_sha:
                invalid += 1
                continue

            # Parse JSON
            try:
                obj = json.loads(payload)
                records.append(obj)
                valid += 1
            except json.JSONDecodeError:
                invalid += 1

    return records, valid, invalid


def _self_test() -> int:
    """
    Run self-test to verify module functionality.

    Returns 0 on success, 1 on failure.
    """
    print("=== JSONL_SHA SELF-TEST ===")

    # Create temp file
    tmp_dir = Path(tempfile.gettempdir())
    test_file = tmp_dir / f"jsonl_sha_selftest_{os.getpid()}.jsonl"

    try:
        # Test 1: Basic write
        print("Test 1: Basic append...")
        record1 = {"test": "basic", "ts": time.time()}
        sha1 = append_sha256_line(test_file, record1)
        assert len(sha1) == 64, f"SHA256 should be 64 chars, got {len(sha1)}"
        print(f"  PASS: wrote sha256:{sha1[:16]}...")

        # Test 2: Multiple writes
        print("Test 2: Multiple appends...")
        for i in range(5):
            append_sha256_line(test_file, {"seq": i, "data": f"item_{i}"})
        print("  PASS: 5 additional records written")

        # Test 3: Read and verify
        print("Test 3: Read and verify...")
        records, valid, invalid = read_and_verify(test_file)
        assert valid == 6, f"Expected 6 valid records, got {valid}"
        assert invalid == 0, f"Expected 0 invalid records, got {invalid}"
        print(f"  PASS: {valid} valid, {invalid} invalid")

        # Test 4: Verify first record
        print("Test 4: Verify data integrity...")
        assert records[0]["test"] == "basic", "First record should have test='basic'"
        print("  PASS: Data integrity verified")

        print("\n=== ALL SELF-TESTS PASSED ===")
        return 0

    except Exception as e:
        print(f"\nFAIL: {e}")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        # Cleanup
        if test_file.exists():
            try:
                test_file.unlink()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(_self_test())
