"""
Atomic I/O primitives with fail-closed semantics.

- atomic_write_bytes/text/json: tmp -> flush -> fsync -> replace
- locked_append_bytes/text: cross-process lock + append + fsync
- FileLock: O_EXCL based locking (works on Windows without external deps)
"""
from __future__ import annotations

import os
import time
import json
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


class StopError(RuntimeError):
    """Fail-closed exception: caller must STOP the run."""


class LockTimeout(StopError):
    """Raised when lock acquisition times out."""


@dataclass(frozen=True)
class AtomicWriteResult:
    path: str
    bytes_written: int
    sha256_hex: str


class FileLock:
    """
    Cross-process lock via exclusive lockfile creation (O_EXCL).
    Works on Windows and does not require external deps.
    """
    def __init__(self, lock_path: Path, timeout_s: float = 10.0, poll_s: float = 0.05) -> None:
        self.lock_path = Path(lock_path)
        self.timeout_s = float(timeout_s)
        self.poll_s = float(poll_s)
        self._fd: Optional[int] = None

    def __enter__(self) -> "FileLock":
        start = time.monotonic()
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

        while True:
            try:
                self._fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                return self
            except FileExistsError:
                if (time.monotonic() - start) >= self.timeout_s:
                    raise LockTimeout(f"Lock timeout: {self.lock_path}")
                time.sleep(self.poll_s)

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            pass
        return False


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write_bytes(path: Path, data: bytes, mode: int = 0o644) -> AtomicWriteResult:
    """
    Atomic file write: tmp -> flush -> fsync -> replace.
    Fail-closed on any error.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    tmp = target.with_suffix(target.suffix + f".tmp.{os.getpid()}.{int(time.time() * 1000)}")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)

    os.replace(str(tmp), str(target))
    return AtomicWriteResult(path=str(target), bytes_written=len(data), sha256_hex=_sha256_hex(data))


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> AtomicWriteResult:
    return atomic_write_bytes(path, text.encode(encoding))


def atomic_write_json(path: Path, obj: Dict[str, Any], encoding: str = "utf-8") -> AtomicWriteResult:
    data = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(encoding)
    return atomic_write_bytes(path, data)


def locked_append_bytes(path: Path, chunk: bytes, lock_timeout_s: float = 10.0) -> int:
    """
    Append with lock + flush + fsync. Fail-closed on errors.
    Returns bytes appended.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.with_suffix(target.suffix + ".lock")

    with FileLock(lock, timeout_s=lock_timeout_s):
        with open(target, "ab") as f:
            f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
            return len(chunk)


def locked_append_text(path: Path, text: str, encoding: str = "utf-8", lock_timeout_s: float = 10.0) -> int:
    return locked_append_bytes(path, text.encode(encoding), lock_timeout_s=lock_timeout_s)


# ═══════════════════════════════════════════════════════════════════════════════
# JSONL with sha256 contracts
# ═══════════════════════════════════════════════════════════════════════════════

def compute_sha256_prefix(data: bytes) -> str:
    """Compute sha256, return 'sha256:' + first 16 hex chars."""
    return f"sha256:{hashlib.sha256(data).hexdigest()[:16]}"


def append_jsonl(
    path: Path,
    entry: Dict[str, Any],
    *,
    add_timestamp: bool = True,
    add_sha256: bool = True,
    lock_timeout_s: float = 10.0,
) -> str:
    """
    Append JSON entry to JSONL file with sha256 contract.

    Returns: sha256 prefix string

    Guarantees:
    - Atomic append with fsync
    - sha256 hash embedded in entry
    - Cross-process locking
    """
    from datetime import datetime, timezone

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Add timestamp if missing
    if add_timestamp and "timestamp" not in entry:
        entry["timestamp"] = datetime.now(timezone.utc).isoformat()

    # Compute sha256 of content (without sha256 field)
    content_for_hash = json.dumps(entry, sort_keys=True, ensure_ascii=False)
    sha = compute_sha256_prefix(content_for_hash.encode("utf-8"))

    if add_sha256:
        entry["sha256"] = sha

    # Serialize
    line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"

    # Atomic append with lock
    locked_append_bytes(path, line.encode("utf-8"), lock_timeout_s=lock_timeout_s)

    return sha


def read_jsonl_validated(
    path: Path,
    *,
    require_sha256: bool = False,
    skip_invalid: bool = True,
):
    """
    Read JSONL file with sha256 validation.

    Yields valid entries. Skips or raises on invalid.
    """
    import logging
    logger = logging.getLogger(__name__)

    path = Path(path)
    if not path.exists():
        return

    with open(path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue

            try:
                entry = json.loads(line)
            except json.JSONDecodeError as e:
                if skip_invalid:
                    logger.warning(f"{path}:{line_num}: Invalid JSON: {e}")
                    continue
                raise StopError(f"{path}:{line_num}: Invalid JSON: {e}")

            # Validate sha256 if present
            if "sha256" in entry:
                stored_sha = entry["sha256"]
                entry_copy = {k: v for k, v in entry.items() if k != "sha256"}
                content = json.dumps(entry_copy, sort_keys=True, ensure_ascii=False)
                computed_sha = compute_sha256_prefix(content.encode("utf-8"))

                if stored_sha != computed_sha:
                    if skip_invalid:
                        logger.warning(f"{path}:{line_num}: SHA256 mismatch")
                        continue
                    raise StopError(f"{path}:{line_num}: SHA256 mismatch")

            elif require_sha256:
                if skip_invalid:
                    logger.warning(f"{path}:{line_num}: Missing sha256")
                    continue
                raise StopError(f"{path}:{line_num}: Missing sha256")

            yield entry


def log_trade_event(
    event_type: str,
    data: Dict[str, Any],
    *,
    trades_file: Path = None,
) -> str:
    """
    Log trade event with full sha256 contract.

    event_type: ORDER, CLOSE, SIGNAL, SKIP_*, ERROR_*
    """
    if trades_file is None:
        trades_file = Path("state/ai/autotrader/trades.jsonl")

    entry = {
        "event": event_type,
        "data": data,
    }

    return append_jsonl(trades_file, entry)
