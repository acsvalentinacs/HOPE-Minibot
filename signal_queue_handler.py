#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
signal_queue_handler.py

Безопасная очередь сигналов для ExecutionEngine v5.

Особенности:
- lock-файл .lock для защиты от одновременной записи/чтения;
- offset-файл .offset для чтения "хвоста" без пересчитывания всего файла;
- не зависит от core.types.TradeSignal: использует свой QueueSignal DTO.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union

from minibot.core.types import TradeSide

LOG = logging.getLogger("minibot.signal_queue_handler")


@dataclass
class QueueSignal:
    """Внутренний сигнал очереди для run_live_v5.

    Минимальный набор полей, который нужен ExecutionEngine:
    - ts        — timestamp (float, UNIX)
    - symbol    — "BTCUSDT"
    - side      — TradeSide.LONG/SHORT/CLOSE
    - risk_usd  — размер риска
    - source    — откуда сигнал
    - confidence, reason, version — метаинфа
    """

    ts: float
    symbol: str
    side: TradeSide
    risk_usd: float
    source: str
    confidence: float = 0.5
    reason: str = ""
    version: int = 1


class SignalQueueHandler:
    """Очередь сигналов на базе JSONL-файла.

    Формат строки:
    {
      "v": 1,
      "ts": 1732840800.123,
      "symbol": "BTCUSDT",
      "side": "LONG",
      "risk_usd": 100.0,
      "source": "turbo_scanner",
      "confidence": 0.85,
      "reason": "Bitcoin above EMA200"
    }
    """

    def __init__(self, queue_path: Union[str, Path]) -> None:
        self.queue_path = Path(queue_path)
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)

        self.lock_path = self.queue_path.with_suffix(self.queue_path.suffix + ".lock")
        self.offset_path = self.queue_path.with_suffix(self.queue_path.suffix + ".offset")

        self._last_read_pos: int = 0
        if self.offset_path.exists():
            try:
                self._last_read_pos = int(self.offset_path.read_text(encoding="utf-8").strip())
            except Exception:
                self._last_read_pos = 0

    # ------------------------------------------------------------------ #
    # Lock helpers
    # ------------------------------------------------------------------ #

    def _acquire_lock(self, timeout_sec: float = 5.0) -> bool:
        """Простейший lock-файл через режим 'x' (atomarный create)."""
        start = time.time()
        while True:
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return True
            except FileExistsError:
                # Проверяем "протухший" lock
                try:
                    mtime = self.lock_path.stat().st_mtime
                except FileNotFoundError:
                    continue

                if time.time() - mtime > timeout_sec:
                    LOG.warning("Старый lock-файл %s обнаружен, удаляю", self.lock_path)
                    try:
                        self.lock_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    continue

                if time.time() - start > timeout_sec:
                    LOG.error("Не удалось получить lock %s за %.1fs", self.lock_path, timeout_sec)
                    return False
                time.sleep(0.05)

    def _release_lock(self) -> None:
        try:
            self.lock_path.unlink(missing_ok=True)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Offset helpers
    # ------------------------------------------------------------------ #

    def _save_offset(self) -> None:
        try:
            self.offset_path.write_text(str(self._last_read_pos), encoding="utf-8")
        except Exception:
            LOG.exception("Не удалось сохранить offset в %s", self.offset_path)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def append_signal(self, signal: Union[QueueSignal, dict]) -> None:
        """Добавить сигнал в очередь (конец файла)."""
        if isinstance(signal, QueueSignal):
            data = {
                "v": signal.version,
                "ts": signal.ts,
                "symbol": signal.symbol,
                "side": signal.side.value,
                "risk_usd": signal.risk_usd,
                "source": signal.source,
                "confidence": signal.confidence,
                "reason": signal.reason,
            }
        else:
            # Ожидаем dict со строковыми ключами
            data = dict(signal)
            data.setdefault("v", data.get("version", 1))

        line = json.dumps(data, ensure_ascii=False) + "\n"

        if not self._acquire_lock():
            raise RuntimeError("Не удалось получить lock для записи сигналов")

        try:
            with self.queue_path.open("a", encoding="utf-8") as f:
                f.write(line)
        finally:
            self._release_lock()

    def read_new_signals(self, max_signals: int = 100) -> List[QueueSignal]:
        """Прочитать "хвост" очереди, начиная с последнего offset."""
        if not self.queue_path.exists():
            return []

        if not self._acquire_lock():
            LOG.warning("Не удалось получить lock для чтения, возвращаю пустой список")
            return []

        signals: List[QueueSignal] = []

        try:
            with self.queue_path.open("r", encoding="utf-8") as f:
                f.seek(self._last_read_pos)
                while len(signals) < max_signals:
                    pos_before = f.tell()
                    line = f.readline()
                    if not line:
                        break

                    self._last_read_pos = f.tell()

                    line = line.strip()
                    if not line:
                        continue

                    try:
                        data = json.loads(line)
                    except Exception:
                        LOG.exception("Ошибка парсинга JSON строки очереди: %r", line)
                        continue

                    try:
                        ts = float(data.get("ts", time.time()))
                        symbol = str(data["symbol"])
                        side_raw = str(data.get("side", "LONG"))
                        try:
                            side = TradeSide(side_raw)
                        except ValueError:
                            # Попробуем buy/sell → LONG/SHORT
                            side = TradeSide.LONG if side_raw.lower() in ("buy", "long") else TradeSide.SHORT

                        risk_usd = float(data.get("risk_usd", 0.0))
                        source = str(data.get("source", "unknown"))
                        confidence = float(data.get("confidence", 0.5))
                        reason = str(data.get("reason", ""))
                        version = int(data.get("v", data.get("version", 1)))

                        sig = QueueSignal(
                            ts=ts,
                            symbol=symbol,
                            side=side,
                            risk_usd=risk_usd,
                            source=source,
                            confidence=confidence,
                            reason=reason,
                            version=version,
                        )
                        signals.append(sig)
                    except Exception:
                        LOG.exception("Ошибка преобразования данных очереди в QueueSignal: %r", data)
                        # В случае ошибки не откатываем offset, просто идём дальше

            self._save_offset()
        finally:
            self._release_lock()

        return signals

    def reset_offset(self) -> None:
        """Сброс offset (перечитывать файл с начала)."""
        self._last_read_pos = 0
        self._save_offset()
