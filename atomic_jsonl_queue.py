"""Atomic JSONL queue with Windows locking."""
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

try:
    import msvcrt
except ImportError:
    msvcrt = None


class QueueError(RuntimeError):
    pass


def header_guard(path: str, fix: bool = False) -> None:
    abs_path = os.path.abspath(path)
    header = {"_header": "AtomicJsonlQueue", "version": 1, "created_utc": int(time.time())}
    header_line = json.dumps(header, ensure_ascii=False) + "\n"
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    if not os.path.exists(abs_path) or os.path.getsize(abs_path) == 0:
        with open(abs_path, "wb") as f:
            f.write(header_line.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        return
    with open(abs_path, "rb") as f:
        first = f.readline()
    try:
        obj = json.loads(first.decode("utf-8"))
        if obj.get("_header") == "AtomicJsonlQueue":
            return
    except json.JSONDecodeError as e:
        logger.warning("header_guard: invalid JSON in header line: %s", e)
    except UnicodeDecodeError as e:
        logger.warning("header_guard: encoding error in header: %s", e)
    if not fix:
        raise QueueError(f"header_guard failed: {path}")
    tmp = f"{abs_path}.tmp.{os.getpid()}"
    with open(abs_path, "rb") as src, open(tmp, "wb") as dst:
        dst.write(header_line.encode("utf-8"))
        dst.write(src.read())
        dst.flush()
        os.fsync(dst.fileno())
    os.replace(tmp, abs_path)


@dataclass
class AtomicJsonlQueue:
    path: str

    def __post_init__(self) -> None:
        header_guard(self.path, fix=True)

    def append(self, record: Dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False) + "\n"
        abs_path = os.path.abspath(self.path)
        with open(abs_path, "ab") as f:
            if msvcrt:
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                except OSError as e:
                    logger.warning("append: failed to acquire lock: %s", e)
            f.write(line.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
            if msvcrt:
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError as e:
                    logger.warning("append: failed to release lock: %s", e)

    def read_all(self) -> List[Dict[str, Any]]:
        abs_path = os.path.abspath(self.path)
        if not os.path.exists(abs_path):
            return []
        records = []
        with open(abs_path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("_header"):
                        continue
                    records.append(obj)
                except json.JSONDecodeError as e:
                    logger.warning("read_all: invalid JSON at line %d: %s", line_num, e)
        return records
