"""
SmartSignalQueueV2 для HOPE v5 (v2.1-prio).

Задачи:
- читать state/signals_v5.jsonl инкрементально (по offset)
- фильтровать старые сигналы по TTL
- дедупликация в окне времени
- приоритет: CLOSE > LONG/SHORT
- НЕ выдавать новые LONG/SHORT, если уже есть открытая позиция
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


logger = logging.getLogger(__name__)


@dataclass
class SignalV5:
    raw: Dict[str, Any]

    @property
    def symbol(self) -> str:
        return str(self.raw.get("symbol", "")).upper()

    @property
    def side(self) -> str:
        return str(self.raw.get("side", "")).upper()

    @property
    def ts(self) -> float:
        try:
            return float(self.raw.get("ts", 0.0))
        except Exception:
            return 0.0

    @property
    def signal_id(self) -> str:
        sid = self.raw.get("signal_id")
        if isinstance(sid, str) and sid:
            return sid
        # fallback
        return f"{self.symbol}_{self.side}_{int(self.ts)}"

    @property
    def price(self) -> Optional[float]:
        try:
            p = self.raw.get("price")
            return float(p) if p is not None else None
        except Exception:
            return None


class SmartSignalQueueV2:
    """
    Очередь сигналов, ориентированная на Engine:

    - читает jsonl-файл инкрементально;
    - кладёт новые сигналы во внутренний буфер;
    - при get_next_for_engine(has_open_position=True/False) отдаёт:
        1) сначала CLOSE;
        2) потом LONG/SHORT, но только если нет открытой позиции.
    """

    def __init__(
        self,
        filepath: str = "state/signals_v5.jsonl",
        *args,
        **kwargs,
    ) -> None:
        """
        Поддерживает гибкий вызов:

        SmartSignalQueueV2(filepath, ttl_ms, dedup_window_ms, logger=...)
        или SmartSignalQueueV2(filepath=..., ttl_ms=..., dedup_window_ms=..., logger=...)
        """

        # Разбираем позиционные и именованные аргументы под ttl/dedup/logger
        ttl_ms: Optional[int] = None
        dedup_window_ms: Optional[int] = None
        log_obj: Optional[logging.Logger] = None

        if args:
            if len(args) >= 1:
                try:
                    ttl_ms = int(args[0])
                except Exception:
                    ttl_ms = None
            if len(args) >= 2:
                try:
                    dedup_window_ms = int(args[1])
                except Exception:
                    dedup_window_ms = None

        if "ttl_ms" in kwargs:
            try:
                ttl_ms = int(kwargs.pop("ttl_ms"))
            except Exception:
                pass
        if "dedup_window_ms" in kwargs:
            try:
                dedup_window_ms = int(kwargs.pop("dedup_window_ms"))
            except Exception:
                pass
        if "logger" in kwargs:
            log_obj = kwargs.pop("logger")

        # Значения по умолчанию
        if ttl_ms is None:
            ttl_ms = 5 * 60 * 1000  # 5 минут
        if dedup_window_ms is None:
            dedup_window_ms = 60 * 1000  # 60 секунд

        self.filepath = Path(filepath)
        self.ttl_sec: float = ttl_ms / 1000.0
        self.dedup_window_sec: float = dedup_window_ms / 1000.0
        self.logger = log_obj or logger

        self._file = None
        self._offset: int = 0
        self._buffer: List[SignalV5] = []
        # для дедупликации: ключ -> ts_последнего
        self._seen: Dict[Tuple[str, str], float] = {}
        self._last_gc_ts: float = 0.0

        self.logger.info(
            "SmartSignalQueueV2 инициализирована. file=%s ttl_sec=%.1f dedup_window_sec=%.1f",
            self.filepath,
            self.ttl_sec,
            self.dedup_window_sec,
        )

    # --------- Файлы / чтение ---------

    def _ensure_file_open(self) -> None:
        if self._file is not None:
            return
        if not self.filepath.exists():
            # Файл ещё не создан — это нормальная ситуация на старте.
            self._file = None
            return
        self._file = self.filepath.open("r", encoding="utf-8", errors="ignore")
        self._file.seek(self._offset)

    def _read_new_lines(self) -> None:
        """
        Читает новые строки из файла и добавляет валидные сигналы в буфер.
        """
        now = time.time()
        self._ensure_file_open()
        if self._file is None:
            return

        while True:
            pos_before = self._file.tell()
            line = self._file.readline()
            if not line:
                # EOF
                break

            self._offset = self._file.tell()

            line = line.strip()
            if not line:
                continue

            try:
                raw = json.loads(line)
            except Exception as e:
                self.logger.warning("SmartSignalQueueV2: не удалось распарсить json: %s", e)
                continue

            sig = SignalV5(raw)

            # TTL-фильтр: если сигнал уже устарел — пропускаем
            if sig.ts and (now - sig.ts) > self.ttl_sec:
                self.logger.debug(
                    "SmartSignalQueueV2: пропуск сигнала (TTL) %s %s ts=%.0f (age=%.1fs)",
                    sig.symbol,
                    sig.side,
                    sig.ts,
                    now - sig.ts,
                )
                continue

            # Дедупликация по (symbol, side) в окне dedup_window_sec
            key = (sig.symbol, sig.side)
            last_seen = self._seen.get(key)
            if last_seen is not None and (sig.ts - last_seen) < self.dedup_window_sec:
                self.logger.debug(
                    "SmartSignalQueueV2: дубликат сигнала %s %s (dt=%.1fs) — игнор",
                    sig.symbol,
                    sig.side,
                    sig.ts - last_seen,
                )
                continue

            self._seen[key] = sig.ts
            self._buffer.append(sig)

            self.logger.info(
                "[QUEUE] received signal: id=%s symbol=%s side=%s price=%s ts=%.0f",
                sig.signal_id,
                sig.symbol,
                sig.side,
                sig.price,
                sig.ts,
            )

        self._gc_seen(now)

    def _gc_seen(self, now: float) -> None:
        """
        Периодически чистим self._seen от очень старых ключей.
        """
        if (now - self._last_gc_ts) < self.ttl_sec:
            return
        self._last_gc_ts = now
        cutoff = now - (self.ttl_sec * 2.0)
        to_del = [k for k, ts in self._seen.items() if ts < cutoff]
        for k in to_del:
            self._seen.pop(k, None)

    # --------- Основной интерфейс для Engine ---------

    def get_next_for_engine(self, has_open_position: bool) -> Optional[Dict[str, Any]]:
        """
        Главный метод, который должен вызывать run_live_v5.

        Логика:
        - читаем новые строки в буфер;
        - сначала пробуем отдать CLOSE-сигнал;
        - затем, если нет открытой позиции — LONG/SHORT;
        - если ничего подходящего нет — возвращаем None.
        """
        now = time.time()
        self._read_new_lines()

        # 1) приоритет CLOSE
        for idx, sig in enumerate(self._buffer):
            if sig.side == "CLOSE":
                self._buffer.pop(idx)
                self.logger.info(
                    "[QUEUE] dispatch CLOSE: id=%s symbol=%s price=%s",
                    sig.signal_id,
                    sig.symbol,
                    sig.price,
                )
                return sig.raw

        # 2) если уже есть открытая позиция — не отдаём новые LONG/SHORT
        if has_open_position:
            return None

        # 3) LONG/SHORT, если позиции нет
        for idx, sig in enumerate(self._buffer):
            if sig.side in ("LONG", "SHORT"):
                self._buffer.pop(idx)
                self.logger.info(
                    "[QUEUE] dispatch %s: id=%s symbol=%s price=%s",
                    sig.side,
                    sig.signal_id,
                    sig.symbol,
                    sig.price,
                )
                return sig.raw

        return None
