"""SmartSignalQueueV2 v2 - без yield и f.tell() проблем"""
from __future__ import annotations
import json, logging, time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
logger = logging.getLogger(__name__)

@dataclass
class SignalQueueStats:
    accepted: int = 0
    rejected: int = 0
    duplicates: int = 0
    stale: int = 0
    decode_errors: int = 0
    def as_dict(self) -> Dict[str, int]:
        return {"accepted": self.accepted, "rejected": self.rejected, "duplicates": self.duplicates, "stale": self.stale, "decode_errors": self.decode_errors}

class SmartSignalQueueV2:
    def __init__(self, file_path: Path, ttl_seconds: int = 300, dedup_window_ms: int = 500, offset_file: Optional[Path] = None) -> None:
        self.file_path = Path(file_path)
        self.ttl_seconds = ttl_seconds
        self.dedup_window_ms = dedup_window_ms
        if offset_file is None:
            self.offset_file = self.file_path.with_suffix(self.file_path.suffix + ".offset")
        else:
            self.offset_file = Path(offset_file)
        self._offset: int = 0
        self._last_seen: Dict[Tuple[str, str], float] = {}
        self._stats: SignalQueueStats = SignalQueueStats()
        self._load_offset()

    def _load_offset(self) -> None:
        if not self.offset_file.exists():
            self._offset = 0
            return
        try:
            raw = self.offset_file.read_text(encoding="utf-8").strip()
            raw = raw.replace('\ufeff', '')
            self._offset = int(raw) if raw else 0
        except Exception as exc:
            logger.warning("SmartSignalQueueV2: не удалось прочитать offset — начинаю с 0: %s", exc)
            self._offset = 0

    def _save_offset(self, value: int) -> None:
        try:
            self.offset_file.write_text(str(value), encoding="utf-8")
        except Exception as exc:
            logger.warning("SmartSignalQueueV2: не удалось сохранить offset: %s", exc)

    def get_stats(self) -> Dict[str, int]:
        return self._stats.as_dict()

    def pop_batch(self, max_batch: int = 256) -> List[Dict[str, Any]]:
        if not self.file_path.exists():
            return []
        now_ts = time.time()
        batch: List[Tuple[int, float, Dict[str, Any]]] = []
        try:
            with self.file_path.open("r", encoding="utf-8") as f:
                f.seek(self._offset)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError as exc:
                        self._stats.decode_errors += 1
                        self._stats.rejected += 1
                        continue
                    symbol = str(raw.get("symbol") or "").strip().upper()
                    side_str = str(raw.get("side") or "").strip().upper()
                    ts = float(raw.get("ts") or 0.0)
                    if not symbol or not side_str:
                        self._stats.rejected += 1
                        continue
                    if ts and (now_ts - ts) > self.ttl_seconds:
                        self._stats.stale += 1
                        self._stats.rejected += 1
                        continue
                    key = (symbol, side_str)
                    last_ts = self._last_seen.get(key)
                    if last_ts is not None and ts and (ts - last_ts) * 1000.0 <= self.dedup_window_ms:
                        self._stats.duplicates += 1
                        self._stats.rejected += 1
                        continue
                    if ts:
                        self._last_seen[key] = ts
                    if side_str == "CLOSE":
                        priority = 3
                    elif side_str == "LONG":
                        priority = 2
                    else:
                        priority = 1
                    batch.append((priority, ts, raw))
                    self._stats.accepted += 1
                    if len(batch) >= max_batch:
                        break
                self._offset = f.tell()
                self._save_offset(self._offset)
        except FileNotFoundError:
            return []
        except Exception as exc:
            logger.error("SmartSignalQueueV2: ошибка чтения %s: %s", self.file_path, exc)
            return []
        batch.sort(key=lambda t: (-t[0], t[1] or 0.0))
        return [raw for _, _, raw in batch]
