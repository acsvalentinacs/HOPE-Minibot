# -*- coding: utf-8 -*-
"""
JSONL I/O (P10)

Single blessed JSONL writer.

Contract:
- Append-only (one JSON object per line)
- Fail-closed (any uncertainty -> exception)
- Per-line durability: lock -> write -> flush -> fsync -> unlock
- Explicit schema: required string field "schema"
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

try:
    import msvcrt  # Windows-only
except ImportError:
    msvcrt = None  # type: ignore


class JsonlWriteError(RuntimeError):
    """Raised when JSONL write fails. Fail-closed semantics."""
    pass


@dataclass(frozen=True)
class AppendResult:
    path: Path
    bytes_written: int
    line: str


def _now_ts() -> float:
    return time.time()


def _ts_iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def safe_json_dumps(obj: Dict[str, Any]) -> str:
    """Compact, UTF-8 safe, stable key ordering for easier diffs."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _lock_exclusive(fh, *, timeout_sec: float) -> None:
    """
    Acquire exclusive lock with timeout.

    Uses non-blocking attempts with short sleeps.
    Fail-closed: raises instead of hanging forever.
    """
    if msvcrt is None:
        raise JsonlWriteError("msvcrt not available; cannot lock JSONL on Windows")

    deadline = _now_ts() + max(0.0, float(timeout_sec))
    fh.seek(0)

    while True:
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError as e:
            if _now_ts() >= deadline:
                raise JsonlWriteError(f"Timeout acquiring JSONL lock: {e!r}") from e
            time.sleep(0.02)


def _unlock(fh) -> None:
    """Unlock file (Windows). Best-effort with logging."""
    if msvcrt is None:
        return
    try:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError as e:
        # File handle close will release lock anyway, but log for observability
        import logging
        logging.getLogger(__name__).debug("_unlock: OSError (handle close will release): %s", e)


def append_jsonl(
    path: Path | str,
    obj: Dict[str, Any],
    *,
    schema: str,
    strict: bool = True,
    add_ts_fields: bool = True,
    lock_timeout_sec: float = 5.0,
) -> AppendResult:
    """
    Append exactly one JSON object as one line into a JSONL file.

    Why not temp->replace:
        JSONL is an append-only audit log. The strongest equivalent guarantee
        is exclusive lock + fsync per line.

    Args:
        path: Target JSONL file path
        obj: Dictionary to write (will be augmented with schema/ts)
        schema: Schema identifier (e.g., "hope.verify.audit.v1")
        strict: If True, fail on schema mismatch in obj
        add_ts_fields: If True, add ts and ts_iso if missing
        lock_timeout_sec: Max seconds to wait for lock (fail-closed)

    Returns:
        AppendResult with path, bytes written, and the line content

    Raises:
        JsonlWriteError: On any write failure (fail-closed)
    """
    if not isinstance(schema, str) or not schema.strip():
        raise JsonlWriteError("schema must be a non-empty string")

    if not isinstance(obj, dict):
        raise JsonlWriteError("obj must be a dict")

    out: Dict[str, Any] = dict(obj)

    if strict and "schema" in out and out.get("schema") != schema:
        raise JsonlWriteError(
            f"schema mismatch: obj.schema={out.get('schema')!r} != {schema!r}"
        )

    out["schema"] = schema

    if add_ts_fields:
        ts = float(out.get("ts", _now_ts()))
        out.setdefault("ts", ts)
        out.setdefault("ts_iso", _ts_iso(ts))

    line = safe_json_dumps(out) + "\n"
    data = line.encode("utf-8")

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(p, "a+b") as fh:
            _lock_exclusive(fh, timeout_sec=lock_timeout_sec)
            try:
                fh.seek(0, os.SEEK_END)
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                _unlock(fh)
    except JsonlWriteError:
        raise
    except Exception as e:
        raise JsonlWriteError(f"append_jsonl failed: path={p} err={e!r}") from e

    return AppendResult(path=p, bytes_written=len(data), line=line.rstrip("\n"))
