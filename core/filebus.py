"""
File-based IPC queue (JSONL append-only) with fail-closed semantics.

- Locked append + fsync
- Cursor (byte offset) stored atomically
- Deadletter on contract violations
- Optional ack stream
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from core.io_atomic import FileLock, StopError, atomic_write_json, locked_append_text
from core.contracts import (
    ContractViolation,
    parse_sha256_prefix_line,
    wrap_sha256_prefix_line,
)


@dataclass(frozen=True)
class QueueConfig:
    base_dir: Path
    name: str
    max_inbox_bytes: int = 64 * 1024 * 1024  # 64MB backpressure
    lock_timeout_s: float = 10.0
    hash_exclude_keys: Optional[Set[str]] = None  # e.g. {"id"} if protocol requires


class FileBusQueue:
    """
    File-based IPC queue (JSONL append-only) with:
    - locked append + fsync
    - cursor (byte offset) stored atomically
    - deadletter on contract violations
    - optional ack stream
    """

    def __init__(self, cfg: QueueConfig) -> None:
        self.cfg = cfg
        self.qdir = Path(cfg.base_dir) / cfg.name
        self.inbox = self.qdir / "inbox.jsonl"
        self.acks = self.qdir / "acks.jsonl"
        self.dead = self.qdir / "deadletter.jsonl"
        self.cursor = self.qdir / "cursor.json"
        self.cursor_lock = self.qdir / "cursor.lock"

        self.qdir.mkdir(parents=True, exist_ok=True)
        if not self.cursor.exists():
            atomic_write_json(self.cursor, {"offset": 0, "acked": 0})

    def _backpressure_check(self) -> None:
        if self.inbox.exists() and self.inbox.stat().st_size > self.cfg.max_inbox_bytes:
            raise StopError(f"Queue overflow: {self.cfg.name} size={self.inbox.stat().st_size}")

    def publish(self, event: Dict[str, Any]) -> str:
        self._backpressure_check()
        line = wrap_sha256_prefix_line(event, hash_exclude_keys=self.cfg.hash_exclude_keys) + "\n"
        locked_append_text(self.inbox, line, lock_timeout_s=self.cfg.lock_timeout_s)
        return line

    def ack(self, event_id: str) -> None:
        payload = {"ack_id": event_id}
        line = wrap_sha256_prefix_line(payload, hash_exclude_keys=None) + "\n"
        locked_append_text(self.acks, line, lock_timeout_s=self.cfg.lock_timeout_s)

    def tail(self, max_items: int = 100) -> List[Dict[str, Any]]:
        """
        Read up to max_items events from inbox starting at cursor offset.
        Fail-closed if line violates contract; offending line goes to deadletter.
        """
        if max_items <= 0:
            return []

        with FileLock(self.cursor_lock, timeout_s=self.cfg.lock_timeout_s):
            cur = self._read_cursor()
            offset = int(cur.get("offset", 0))

            if not self.inbox.exists():
                return []

            out: List[Dict[str, Any]] = []
            with open(self.inbox, "rb") as f:
                f.seek(offset)
                for _ in range(max_items):
                    line_bytes = f.readline()
                    if not line_bytes:
                        break
                    try:
                        line = line_bytes.decode("utf-8")
                        obj = parse_sha256_prefix_line(line, hash_exclude_keys=self.cfg.hash_exclude_keys)
                        out.append(obj)
                    except (UnicodeDecodeError, ContractViolation) as e:
                        # deadletter + STOP
                        locked_append_text(self.dead, line_bytes.decode("utf-8", errors="replace"), lock_timeout_s=self.cfg.lock_timeout_s)
                        raise StopError(f"Contract violation in {self.cfg.name}: {e}") from e

                new_offset = f.tell()

            cur["offset"] = new_offset
            atomic_write_json(self.cursor, cur)
            return out

    def _read_cursor(self) -> Dict[str, Any]:
        try:
            return json.loads(self.cursor.read_text(encoding="utf-8"))
        except Exception as e:
            raise StopError(f"Cursor unreadable: {self.cursor} ({e})") from e
